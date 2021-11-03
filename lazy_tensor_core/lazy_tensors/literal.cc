#include "lazy_tensors/literal.h"

#include "lazy_tensors/computation_client/util.h"
#include "lazy_tensors/core/platform/hash.h"
#include "lazy_tensors/shape_util.h"

namespace lazy_tensors {

Literal::Literal(const Shape& shape) : shape_(shape) {
  CHECK(!shape_.IsTuple());
  std::vector<int64_t> dimensions = util::ToVector<int64_t>(shape.sizes());
  at::TensorOptions options(shape.scalar_type());
  value_ = at::empty(dimensions, options);
}

const Shape& Literal::shape() const { return shape_; }

size_t Literal::Hash() const {
  size_t hash_value = ShapeUtil::Hash(shape());
  CHECK(!shape().IsTuple());
  return hash_value;
}

}  // namespace lazy_tensors
