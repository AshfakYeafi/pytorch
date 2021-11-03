#!/usr/bin/env python3
# Welcome to the PyTorch/LTC setup.py.
#
# Environment variables you are probably interested in:
#
#   DEBUG
#     build with -O0 and -g (debug symbols)
#
#   TORCH_LTC_VERSION
#     specify the version of PyTorch/LTC, rather than the hard-coded version
#     in this file; used when we're building binaries for distribution
#
#   VERSIONED_LTC_BUILD
#     creates a versioned build
#
#   TORCH_LTC_PACKAGE_NAME
#     change the package name to something other than 'lazy_tensor_core'
#
#   COMPILE_PARALLEL=1
#     enable parallel compile
#
#   BUILD_CPP_TESTS=1
#     build the C++ tests
#

from __future__ import print_function

from setuptools import setup, find_packages, distutils
from torch.utils.cpp_extension import BuildExtension, CppExtension
import distutils.ccompiler
import distutils.command.clean
import glob
import inspect
import multiprocessing
import multiprocessing.pool
import os
import platform
import re
import shutil
import subprocess
import sys

base_dir = os.path.dirname(os.path.abspath(__file__))
third_party_path = os.path.join(base_dir, 'third_party')


def _get_build_mode():
    for i in range(1, len(sys.argv)):
        if not sys.argv[i].startswith('-'):
            return sys.argv[i]


def _check_env_flag(name, default=''):
    return os.getenv(name, default).upper() in ['ON', '1', 'YES', 'TRUE', 'Y']


def get_git_head_sha(base_dir):
    ltc_git_sha = subprocess.check_output(['git', 'rev-parse', 'HEAD'],
                                          cwd=base_dir).decode('ascii').strip()
    if os.path.isdir(os.path.join(base_dir, '..', '.git')):
        torch_git_sha = subprocess.check_output(['git', 'rev-parse', 'HEAD'],
                                                cwd=os.path.join(
                                                    base_dir,
                                                    '..')).decode('ascii').strip()
    else:
        torch_git_sha = ''
    return ltc_git_sha, torch_git_sha


def get_build_version(ltc_git_sha):
    version = os.getenv('TORCH_LTC_VERSION', '1.9')
    if _check_env_flag('VERSIONED_LTC_BUILD', default='0'):
        try:
            version += '+' + ltc_git_sha[:7]
        except Exception:
            pass
    return version


def create_version_files(base_dir, version, ltc_git_sha, torch_git_sha):
    print('Building lazy_tensor_core version: {}'.format(version))
    print('LTC Commit ID: {}'.format(ltc_git_sha))
    print('PyTorch Commit ID: {}'.format(torch_git_sha))
    py_version_path = os.path.join(base_dir, 'lazy_tensor_core', 'version.py')
    with open(py_version_path, 'w') as f:
        f.write('# Autogenerated file, do not edit!\n')
        f.write("__version__ = '{}'\n".format(version))
        f.write("__ltc_gitrev__ = '{}'\n".format(ltc_git_sha))
        f.write("__torch_gitrev__ = '{}'\n".format(torch_git_sha))

    cpp_version_path = os.path.join(base_dir, 'lazy_tensor_core', 'csrc', 'version.cpp')
    with open(cpp_version_path, 'w') as f:
        f.write('// Autogenerated file, do not edit!\n')
        f.write('#include "lazy_tensor_core/csrc/version.h"\n\n')
        f.write('namespace torch_lazy_tensors {\n\n')
        f.write('const char LTC_GITREV[] = {{"{}"}};\n'.format(ltc_git_sha))
        f.write('const char TORCH_GITREV[] = {{"{}"}};\n\n'.format(torch_git_sha))
        f.write('}  // namespace torch_lazy_tensors\n')


def generate_ltc_aten_code(base_dir):
    generate_code_cmd = [os.path.join(base_dir, 'scripts', 'generate_code.sh')]
    if subprocess.call(generate_code_cmd) != 0:
        print(
            'Failed to generate ATEN bindings: {}'.format(generate_code_cmd),
            file=sys.stderr)
        sys.exit(1)


def _compile_parallel(self,
                      sources,
                      output_dir=None,
                      macros=None,
                      include_dirs=None,
                      debug=0,
                      extra_preargs=None,
                      extra_postargs=None,
                      depends=None):
    # Those lines are copied from distutils.ccompiler.CCompiler directly.
    macros, objects, extra_postargs, pp_opts, build = self._setup_compile(
        output_dir, macros, include_dirs, sources, depends, extra_postargs)
    cc_args = self._get_cc_args(pp_opts, debug, extra_preargs)

    def compile_one(obj):
        try:
            src, ext = build[obj]
        except KeyError:
            return
        self._compile(obj, src, ext, cc_args, extra_postargs, pp_opts)

    list(
        multiprocessing.pool.ThreadPool(multiprocessing.cpu_count()).imap(
            compile_one, objects))
    return objects


# Plant the parallel compile function.
if _check_env_flag('COMPILE_PARALLEL', default='1'):
    try:
        if (inspect.signature(distutils.ccompiler.CCompiler.compile) ==
                inspect.signature(_compile_parallel)):
            distutils.ccompiler.CCompiler.compile = _compile_parallel
    except BaseException:
        pass


class Clean(distutils.command.clean.clean):

    def run(self):
        import glob
        import re
        with open('.gitignore', 'r') as f:
            ignores = f.read()
            pat = re.compile(r'^#( BEGIN NOT-CLEAN-FILES )?')
            for wildcard in filter(None, ignores.split('\n')):
                match = pat.match(wildcard)
                if match:
                    if match.group(1):
                        # Marker is found and stop reading .gitignore.
                        break
                    # Ignore lines which begin with '#'.
                else:
                    for filename in glob.glob(wildcard):
                        try:
                            os.remove(filename)
                        except OSError:
                            shutil.rmtree(filename, ignore_errors=True)

        # It's an old-style class in Python 2.7...
        distutils.command.clean.clean.run(self)


class Build(BuildExtension):

    def run(self):
        # Run the original BuildExtension first. We need this before building
        # the tests.
        BuildExtension.run(self)
        if _check_env_flag('BUILD_CPP_TESTS', default='1'):
            # Build the C++ tests.
            cmd = [os.path.join(base_dir, 'test/cpp/run_tests.sh'), '-B']
            if subprocess.call(cmd) != 0:
                print('Failed to build tests: {}'.format(cmd), file=sys.stderr)
                sys.exit(1)


ltc_git_sha, torch_git_sha = get_git_head_sha(base_dir)
version = get_build_version(ltc_git_sha)

build_mode = _get_build_mode()
if build_mode not in ['clean']:
    # Generate version info (lazy_tensor_core.__version__).
    create_version_files(base_dir, version, ltc_git_sha, torch_git_sha)

    # Generate the code before globbing!
    generate_ltc_aten_code(base_dir)

    computation_client_src = os.path.join(base_dir, 'third_party', 'computation_client')
    computation_client_dst = os.path.join(base_dir, 'lazy_tensors')
    cmd = ['cp', '-r', '-u', '-p', computation_client_src, computation_client_dst]
    if subprocess.call(cmd) != 0:
        print('Failed to build tests: {}'.format(cmd), file=sys.stderr)
        sys.exit(1)

client_files = [
    'third_party/computation_client/env_vars.cc',
    'third_party/computation_client/metrics.cc',
    'third_party/computation_client/metrics_reader.cc',
    'third_party/computation_client/multi_wait.cc',
    'third_party/computation_client/sys_util.cc',
    'third_party/computation_client/thread_pool.cc',
    'third_party/computation_client/triggered_task.cc',
]

# Fetch the sources to be built.
torch_ltc_sources = (
    glob.glob('lazy_tensor_core/csrc/*.cpp') + glob.glob('lazy_tensor_core/csrc/ops/*.cpp') +
    glob.glob('lazy_tensor_core/csrc/compiler/*.cpp') + glob.glob('lazy_tensor_core/csrc/ts_backend/*.cpp') +
    glob.glob('lazy_tensors/client/*.cc') + glob.glob('lazy_tensors/*.cc') +
    glob.glob('lazy_tensors/client/lib/*.cc') + glob.glob('lazy_tensors/core/platform/*.cc') +
    client_files)

# Constant known variables used throughout this file.
lib_path = os.path.join(base_dir, 'lazy_tensor_core/lib')
pytorch_source_path = os.getenv('PYTORCH_SOURCE_PATH',
                                os.path.dirname(base_dir))

# Setup include directories folders.
include_dirs = [
    base_dir,
    pytorch_source_path,
    os.path.join(pytorch_source_path, 'torch/csrc'),
    os.path.join(pytorch_source_path, 'torch/lib/tmp_install/include'),
]

library_dirs = []
library_dirs.append(lib_path)

extra_link_args = []

DEBUG = _check_env_flag('DEBUG')
IS_DARWIN = (platform.system() == 'Darwin')
IS_LINUX = (platform.system() == 'Linux')


def make_relative_rpath(path):
    if IS_DARWIN:
        return '-Wl,-rpath,@loader_path/' + path
    else:
        return '-Wl,-rpath,$ORIGIN/' + path


extra_compile_args = [
    '-std=c++14',
    '-Wno-sign-compare',
    '-Wno-unknown-pragmas',
    '-Wno-return-type',
]

if re.match(r'clang', os.getenv('CC', '')):
    extra_compile_args += [
        '-Wno-macro-redefined',
        '-Wno-return-std-move',
    ]

if DEBUG:
    extra_compile_args += ['-O0', '-g']
    extra_link_args += ['-O0', '-g']
else:
    extra_compile_args += ['-DNDEBUG']

setup(
    name=os.environ.get('TORCH_LTC_PACKAGE_NAME', 'lazy_tensor_core'),
    version=version,
    description='Lazy tensors for PyTorch',
    url='https://github.com/pytorch/ltc',
    author='PyTorch/LTC Dev Team',
    author_email='pytorch-ltc@googlegroups.com',
    # Exclude the build files.
    packages=find_packages(exclude=['build']),
    ext_modules=[
        CppExtension(
            '_LAZYC',
            torch_ltc_sources,
            include_dirs=include_dirs,
            extra_compile_args=extra_compile_args,
            library_dirs=library_dirs,
            extra_link_args=extra_link_args + \
                [make_relative_rpath('lazy_tensor_core/lib')],
        ),
    ],
    package_data={
        'lazy_tensor_core': [
            'lib/*.so*',
        ],
    },
    data_files=[
        'test/cpp/build/test_ptltc',
        'scripts/fixup_binary.py',
    ],
    cmdclass={
        'build_ext': Build,
        'clean': Clean,
    })
