# Copyright 2015 The TensorFlow Authors. All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
# ==============================================================================
"""Normalization layers."""

import tensorflow.compat.v2 as tf
from tensorflow.python.framework import tensor_shape
from keras import backend
from keras import constraints
from keras import initializers
from keras import regularizers
from keras.engine.base_layer import Layer
from keras.engine.input_spec import InputSpec
from keras.utils import control_flow_util
from tensorflow.python.ops.control_flow_ops import get_enclosing_xla_context
from tensorflow.python.platform import tf_logging as logging
from tensorflow.python.util.tf_export import keras_export


class BatchNormalizationBase(Layer):
  r"""Layer that normalizes its inputs.

  Batch normalization applies a transformation that maintains the mean output
  close to 0 and the output standard deviation close to 1.

  Importantly, batch normalization works differently during training and
  during inference.

  **During training** (i.e. when using `fit()` or when calling the layer/model
  with the argument `training=True`), the layer normalizes its output using
  the mean and standard deviation of the current batch of inputs. That is to
  say, for each channel being normalized, the layer returns
  `gamma * (batch - mean(batch)) / sqrt(var(batch) + epsilon) + beta`, where:

  - `epsilon` is small constant (configurable as part of the constructor
  arguments)
  - `gamma` is a learned scaling factor (initialized as 1), which
  can be disabled by passing `scale=False` to the constructor.
  - `beta` is a learned offset factor (initialized as 0), which
  can be disabled by passing `center=False` to the constructor.

  **During inference** (i.e. when using `evaluate()` or `predict()` or when
  calling the layer/model with the argument `training=False` (which is the
  default), the layer normalizes its output using a moving average of the
  mean and standard deviation of the batches it has seen during training. That
  is to say, it returns
  `gamma * (batch - self.moving_mean) / sqrt(self.moving_var + epsilon) + beta`.

  `self.moving_mean` and `self.moving_var` are non-trainable variables that
  are updated each time the layer in called in training mode, as such:

  - `moving_mean = moving_mean * momentum + mean(batch) * (1 - momentum)`
  - `moving_var = moving_var * momentum + var(batch) * (1 - momentum)`

  As such, the layer will only normalize its inputs during inference
  *after having been trained on data that has similar statistics as the
  inference data*.

  Args:
    axis: Integer or a list of integers, the axis that should be normalized
    (typically the features axis). For instance, after a `Conv2D` layer with
      `data_format="channels_first"`, set `axis=1` in `BatchNormalization`.
    momentum: Momentum for the moving average.
    epsilon: Small float added to variance to avoid dividing by zero.
    center: If True, add offset of `beta` to normalized tensor. If False, `beta`
      is ignored.
    scale: If True, multiply by `gamma`. If False, `gamma` is not used. When the
      next layer is linear (also e.g. `nn.relu`), this can be disabled since the
      scaling will be done by the next layer.
    beta_initializer: Initializer for the beta weight.
    gamma_initializer: Initializer for the gamma weight.
    moving_mean_initializer: Initializer for the moving mean.
    moving_variance_initializer: Initializer for the moving variance.
    beta_regularizer: Optional regularizer for the beta weight.
    gamma_regularizer: Optional regularizer for the gamma weight.
    beta_constraint: Optional constraint for the beta weight.
    gamma_constraint: Optional constraint for the gamma weight.
    renorm: Whether to use [Batch Renormalization](
      https://arxiv.org/abs/1702.03275). This adds extra variables during
        training. The inference is the same for either value of this parameter.
    renorm_clipping: A dictionary that may map keys 'rmax', 'rmin', 'dmax' to
      scalar `Tensors` used to clip the renorm correction. The correction `(r,
      d)` is used as `corrected_value = normalized_value * r + d`, with `r`
      clipped to [rmin, rmax], and `d` to [-dmax, dmax]. Missing rmax, rmin,
      dmax are set to inf, 0, inf, respectively.
    renorm_momentum: Momentum used to update the moving means and standard
      deviations with renorm. Unlike `momentum`, this affects training and
      should be neither too small (which would add noise) nor too large (which
      would give stale estimates). Note that `momentum` is still applied to get
      the means and variances for inference.
    fused: if `True`, use a faster, fused implementation, or raise a ValueError
      if the fused implementation cannot be used. If `None`, use the faster
      implementation if possible. If False, do not used the fused
      implementation.
      Note that in TensorFlow 1.x, the meaning of `fused=True` is different:
      if `False`, the layer uses the system-recommended implementation.
    trainable: Boolean, if `True` the variables will be marked as trainable.
    virtual_batch_size: An `int`. By default, `virtual_batch_size` is `None`,
      which means batch normalization is performed across the whole batch. When
      `virtual_batch_size` is not `None`, instead perform "Ghost Batch
      Normalization", which creates virtual sub-batches which are each
      normalized separately (with shared gamma, beta, and moving statistics).
      Must divide the actual batch size during execution.
    adjustment: A function taking the `Tensor` containing the (dynamic) shape of
      the input tensor and returning a pair (scale, bias) to apply to the
      normalized values (before gamma and beta), only during training. For
      example, if `axis=-1`,
        `adjustment = lambda shape: (
          tf.random.uniform(shape[-1:], 0.93, 1.07),
          tf.random.uniform(shape[-1:], -0.1, 0.1))` will scale the normalized
            value by up to 7% up or down, then shift the result by up to 0.1
            (with independent scaling and bias for each feature but shared
            across all examples), and finally apply gamma and/or beta. If
            `None`, no adjustment is applied. Cannot be specified if
            virtual_batch_size is specified.

  Call arguments:
    inputs: Input tensor (of any rank).
    training: Python boolean indicating whether the layer should behave in
      training mode or in inference mode.
      - `training=True`: The layer will normalize its inputs using the mean and
        variance of the current batch of inputs.
      - `training=False`: The layer will normalize its inputs using the mean and
        variance of its moving statistics, learned during training.

  Input shape:
    Arbitrary. Use the keyword argument `input_shape` (tuple of
    integers, does not include the samples axis) when using this layer as the
    first layer in a model.

  Output shape:
    Same shape as input.

  Reference:
    - [Ioffe and Szegedy, 2015](https://arxiv.org/abs/1502.03167).
  """

  # By default, the base class uses V2 behavior. The BatchNormalization V1
  # subclass sets this to False to use the V1 behavior.
  _USE_V2_BEHAVIOR = True

  def __init__(self,
               axis=-1,
               momentum=0.99,
               epsilon=1e-3,
               center=True,
               scale=True,
               beta_initializer='zeros',
               gamma_initializer='ones',
               moving_mean_initializer='zeros',
               moving_variance_initializer='ones',
               beta_regularizer=None,
               gamma_regularizer=None,
               beta_constraint=None,
               gamma_constraint=None,
               renorm=False,
               renorm_clipping=None,
               renorm_momentum=0.99,
               fused=None,
               trainable=True,
               virtual_batch_size=None,
               adjustment=None,
               name=None,
               **kwargs):
    super(BatchNormalizationBase, self).__init__(name=name, **kwargs)
    if isinstance(axis, (list, tuple)):
      self.axis = axis[:]
    elif isinstance(axis, int):
      self.axis = axis
    else:
      raise TypeError('Expected an int or a list/tuple of ints for the '
                      'argument \'axis\', but received: %r' % axis)
    self.momentum = momentum
    self.epsilon = epsilon
    self.center = center
    self.scale = scale
    self.beta_initializer = initializers.get(beta_initializer)
    self.gamma_initializer = initializers.get(gamma_initializer)
    self.moving_mean_initializer = initializers.get(moving_mean_initializer)
    self.moving_variance_initializer = initializers.get(
        moving_variance_initializer)
    self.beta_regularizer = regularizers.get(beta_regularizer)
    self.gamma_regularizer = regularizers.get(gamma_regularizer)
    self.beta_constraint = constraints.get(beta_constraint)
    self.gamma_constraint = constraints.get(gamma_constraint)
    self.renorm = renorm
    self.virtual_batch_size = virtual_batch_size
    self.adjustment = adjustment
    if self._USE_V2_BEHAVIOR:
      if fused:
        self._raise_if_fused_cannot_be_used()
      # We leave fused as None if self._fused_can_be_used()==True, since we
      # still may set it to False in self.build() if the input rank is not 4.
      elif fused is None and not self._fused_can_be_used():
        fused = False
    elif fused is None:
      fused = True
    self.supports_masking = True

    self.fused = fused
    self._bessels_correction_test_only = True
    self.trainable = trainable

    if renorm:
      renorm_clipping = renorm_clipping or {}
      keys = ['rmax', 'rmin', 'dmax']
      if set(renorm_clipping) - set(keys):
        raise ValueError('renorm_clipping %s contains keys not in %s' %
                         (renorm_clipping, keys))
      self.renorm_clipping = renorm_clipping
      self.renorm_momentum = renorm_momentum

  def _raise_if_fused_cannot_be_used(self):
    """Raises a ValueError if fused implementation cannot be used.

    In addition to the checks done in this function, the input tensors rank must
    be 4 or 5. The input rank check can only be done once the input shape is
    known.
    """
    # Note the ValueErrors in this function are caught and not reraised in
    # _fused_can_be_used(). No other exception besides ValueError should be
    # raised here.

    # Currently fused batch norm doesn't support renorm. It also only supports a
    # channel dimension on axis 1 or 3 (rank=4) / 1 or 4 (rank5), when no
    # virtual batch size or adjustment is used.
    if self.renorm:
      raise ValueError('Passing both `fused=True` and `renorm=True` is '
                       'not supported')
    axis = [self.axis] if isinstance(self.axis, int) else self.axis
    # Axis -3 is equivalent to 1, and axis -1 is equivalent to 3, when the
    # input rank is 4. Similarly, the valid axis is -4, -1, 1, 4 when the rank
    # is 5. The combination of ranks and axes will be checked later.
    if len(axis) > 1 or axis[0] not in (-4, -3, -1, 1, 3, 4):
      raise ValueError('Passing `fused=True` is only supported when axis is 1 '
                       'or 3 for input rank = 4 or 1 or 4 for input rank = 5. '
                       'Got axis %s' % (axis,))
    if self.virtual_batch_size is not None:
      raise ValueError('Passing `fused=True` is not supported when '
                       '`virtual_batch_size` is specified.')
    if self.adjustment is not None:
      raise ValueError('Passing `fused=True` is not supported when '
                       '`adjustment` is specified.')
    # TODO(reedwm): Support fp64 in FusedBatchNorm then remove this check.
    if self._compute_dtype not in ('float16', 'bfloat16', 'float32', None):
      raise ValueError(
          'Passing `fused=True` is only supported when the compute '
          'dtype is float16, bfloat16, or float32. Got dtype: %s' %
          (self._compute_dtype,))

  def _fused_can_be_used(self):
    try:
      self._raise_if_fused_cannot_be_used()
      return True
    except ValueError:
      return False

  @property
  def trainable(self):
    return self._trainable

  @trainable.setter
  def trainable(self, value):
    self._trainable = value

  @property
  def _param_dtype(self):
    # Raise parameters of fp16 batch norm to fp32
    if self.dtype == tf.float16 or self.dtype == tf.bfloat16:
      return tf.float32
    else:
      return self.dtype or tf.float32

  def _support_zero_size_input(self):
    return tf.distribute.has_strategy() and getattr(
        tf.distribute.get_strategy().extended,
        'experimental_enable_get_next_as_optional', False)

  def build(self, input_shape):
    input_shape = tf.TensorShape(input_shape)
    if not input_shape.ndims:
      raise ValueError('Input has undefined rank.')
    ndims = len(input_shape)

    # Convert axis to list and resolve negatives
    if isinstance(self.axis, int):
      self.axis = [self.axis]

    for idx, x in enumerate(self.axis):
      if x < 0:
        self.axis[idx] = ndims + x

    # Validate axes
    for x in self.axis:
      if x < 0 or x >= ndims:
        raise ValueError('Invalid axis: %s' % (self.axis,))
    if len(self.axis) != len(set(self.axis)):
      raise ValueError('Duplicate axis: %s' % (self.axis,))

    if self.virtual_batch_size is not None:
      if self.virtual_batch_size <= 0:
        raise ValueError('virtual_batch_size must be a positive integer that '
                         'divides the true batch size of the input tensor')
      # If using virtual batches, the first dimension must be the batch
      # dimension and cannot be the batch norm axis
      if 0 in self.axis:
        raise ValueError('When using virtual_batch_size, the batch dimension '
                         'must be 0 and thus axis cannot include 0. '
                         'Received axis=%s' % (self.axis,))
      if self.adjustment is not None:
        raise ValueError('When using virtual_batch_size, adjustment cannot '
                         'be specified')

    if self.fused in (None, True):
      # TODO(yaozhang): if input is not 4D, reshape it to 4D and reshape the
      # output back to its original shape accordingly.
      if self._USE_V2_BEHAVIOR:
        if self.fused is None:
          self.fused = ndims in (4, 5)
        elif self.fused and ndims not in (4, 5):
          raise ValueError('Batch normalization layers with `fused=True` only '
                           'support 4D or 5D input tensors. '
                           'Received tensor with shape: %s' %
                           (tuple(input_shape),))
      else:
        assert self.fused is not None
        self.fused = (ndims in (4, 5) and self._fused_can_be_used())
      # TODO(chrisying): fused batch norm is currently not supported for
      # multi-axis batch norm and by extension virtual batches. In some cases,
      # it might be possible to use fused batch norm but would require reshaping
      # the Tensor to 4D with the axis in 1 or 3 (preferred 1) which is
      # particularly tricky. A compromise might be to just support the most
      # common use case (turning 5D w/ virtual batch to NCHW)

    if self.fused:
      if self.axis == [1] and ndims == 4:
        self._data_format = 'NCHW'
      elif self.axis == [1] and ndims == 5:
        self._data_format = 'NCDHW'
      elif self.axis == [3] and ndims == 4:
        self._data_format = 'NHWC'
      elif self.axis == [4] and ndims == 5:
        self._data_format = 'NDHWC'
      elif ndims == 5:
        # 5D tensors that can be passed in but should not use fused batch norm
        # due to unsupported axis.
        self.fused = False
      else:
        if ndims == 4:
          raise ValueError(
              'Unsupported axis. The use of `fused=True` is only possible with '
              '`axis=1` or `axis=3` for 4D input tensors. Received '
              'axis=%s' % (self.axis,))
        else:
          raise ValueError(
              'Unsupported axis. The use of `fused=True` is only possible with '
              '`axis=1` or `axis=4` for 5D input tensors. Received '
              'axis=%s' % (self.axis,))

    axis_to_dim = {x: input_shape.dims[x].value for x in self.axis}
    for x in axis_to_dim:
      if axis_to_dim[x] is None:
        raise ValueError('Input has undefined `axis` dimension. Received input '
                         'with shape %s. Axis value: %s' %
                         (tuple(input_shape), self.axis))
    self.input_spec = InputSpec(ndim=ndims, axes=axis_to_dim)

    if len(axis_to_dim) == 1 and self.virtual_batch_size is None:
      # Single axis batch norm (most common/default use-case)
      param_shape = (list(axis_to_dim.values())[0],)
    else:
      # Parameter shape is the original shape but with 1 in all non-axis dims
      param_shape = [
          axis_to_dim[i] if i in axis_to_dim else 1 for i in range(ndims)
      ]
      if self.virtual_batch_size is not None:
        # When using virtual batches, add an extra dim at index 1
        param_shape.insert(1, 1)
        for idx, x in enumerate(self.axis):
          self.axis[idx] = x + 1  # Account for added dimension

    if self.scale:
      self.gamma = self.add_weight(
          name='gamma',
          shape=param_shape,
          dtype=self._param_dtype,
          initializer=self.gamma_initializer,
          regularizer=self.gamma_regularizer,
          constraint=self.gamma_constraint,
          trainable=True,
          experimental_autocast=False)
    else:
      self.gamma = None
      if self.fused:
        self._gamma_const = backend.constant(
            1.0, dtype=self._param_dtype, shape=param_shape)

    if self.center:
      self.beta = self.add_weight(
          name='beta',
          shape=param_shape,
          dtype=self._param_dtype,
          initializer=self.beta_initializer,
          regularizer=self.beta_regularizer,
          constraint=self.beta_constraint,
          trainable=True,
          experimental_autocast=False)
    else:
      self.beta = None
      if self.fused:
        self._beta_const = backend.constant(
            0.0, dtype=self._param_dtype, shape=param_shape)

    try:
      # Disable variable partitioning when creating the moving mean and variance
      if hasattr(self, '_scope') and self._scope:
        partitioner = self._scope.partitioner
        self._scope.set_partitioner(None)
      else:
        partitioner = None
      self.moving_mean = self.add_weight(
          name='moving_mean',
          shape=param_shape,
          dtype=self._param_dtype,
          initializer=self.moving_mean_initializer,
          synchronization=tf.VariableSynchronization.ON_READ,
          trainable=False,
          aggregation=tf.compat.v1.VariableAggregation.MEAN,
          experimental_autocast=False)

      self.moving_variance = self.add_weight(
          name='moving_variance',
          shape=param_shape,
          dtype=self._param_dtype,
          initializer=self.moving_variance_initializer,
          synchronization=tf.VariableSynchronization.ON_READ,
          trainable=False,
          aggregation=tf.compat.v1.VariableAggregation.MEAN,
          experimental_autocast=False)

      if self.renorm:
        # In batch renormalization we track the inference moving stddev instead
        # of the moving variance to more closely align with the paper.
        def moving_stddev_initializer(*args, **kwargs):
          return tf.sqrt(
              self.moving_variance_initializer(*args, **kwargs))

        with tf.distribute.get_strategy(
        ).extended.colocate_vars_with(self.moving_variance):
          self.moving_stddev = self.add_weight(
              name='moving_stddev',
              shape=param_shape,
              dtype=self._param_dtype,
              initializer=moving_stddev_initializer,
              synchronization=tf.VariableSynchronization.ON_READ,
              trainable=False,
              aggregation=tf.compat.v1.VariableAggregation.MEAN,
              experimental_autocast=False)

        # Create variables to maintain the moving mean and standard deviation.
        # These are used in training and thus are different from the moving
        # averages above. The renorm variables are colocated with moving_mean
        # and moving_stddev.
        # NOTE: below, the outer `with device` block causes the current device
        # stack to be cleared. The nested ones use a `lambda` to set the desired
        # device and ignore any devices that may be set by the custom getter.
        def _renorm_variable(name,
                             shape,
                             initializer=tf.compat.v1.zeros_initializer()):
          """Create a renorm variable."""
          var = self.add_weight(
              name=name,
              shape=shape,
              dtype=self._param_dtype,
              initializer=initializer,
              synchronization=tf.VariableSynchronization.ON_READ,
              trainable=False,
              aggregation=tf.compat.v1.VariableAggregation.MEAN,
              experimental_autocast=False)
          return var

        with tf.distribute.get_strategy(
        ).extended.colocate_vars_with(self.moving_mean):
          self.renorm_mean = _renorm_variable('renorm_mean', param_shape,
                                              self.moving_mean_initializer)
        with tf.distribute.get_strategy(
        ).extended.colocate_vars_with(self.moving_stddev):
          self.renorm_stddev = _renorm_variable('renorm_stddev', param_shape,
                                                moving_stddev_initializer)
    finally:
      if partitioner:
        self._scope.set_partitioner(partitioner)
    self.built = True

  def _assign_moving_average(self, variable, value, momentum, inputs_size):

    def calculate_update_delta():
      decay = tf.convert_to_tensor(
          1.0 - momentum, name='decay')
      if decay.dtype != variable.dtype.base_dtype:
        decay = tf.cast(decay, variable.dtype.base_dtype)
      update_delta = (variable - tf.cast(value, variable.dtype)) * decay
      if inputs_size is not None:
        update_delta = tf.compat.v1.where(inputs_size > 0, update_delta,
                                       backend.zeros_like(update_delta))
      return update_delta

    with backend.name_scope('AssignMovingAvg') as scope:
      if tf.compat.v1.executing_eagerly_outside_functions():
        return variable.assign_sub(calculate_update_delta(), name=scope)
      else:
        with tf.compat.v1.colocate_with(variable):  # pylint: disable=protected-access
          return tf.compat.v1.assign_sub(
              variable, calculate_update_delta(), name=scope)

  def _assign_new_value(self, variable, value):
    with backend.name_scope('AssignNewValue') as scope:
      if tf.compat.v1.executing_eagerly_outside_functions():
        return variable.assign(value, name=scope)
      else:
        with tf.compat.v1.colocate_with(variable):  # pylint: disable=protected-access
          return tf.compat.v1.assign(variable, value, name=scope)

  def _fused_batch_norm(self, inputs, training):
    """Returns the output of fused batch norm."""
    beta = self.beta if self.center else self._beta_const
    gamma = self.gamma if self.scale else self._gamma_const

    # TODO(b/129279393): Support zero batch input in non DistributionStrategy
    # code as well.
    if self._support_zero_size_input():
      # Keras assumes that batch dimension is the first dimension for Batch
      # Normalization.
      input_batch_size = tf.compat.v1.shape(inputs)[0]
    else:
      input_batch_size = None

    # TODO(rmlarsen): Support using fused avg updates for non-eager execution
    # after fixing graph pattern matching and enabling fused_batch_norm to
    # take exponential_avg_factor as a tensor input.
    use_fused_avg_updates = (
        tf.compat.v1.executing_eagerly_outside_functions() and
        isinstance(self.momentum, (float, int)) and
        get_enclosing_xla_context() is None)
    if use_fused_avg_updates:
      exponential_avg_factor = 1.0 - self.momentum
    else:
      exponential_avg_factor = None

    def _maybe_add_or_remove_bessels_correction(variance, remove=True):
      r"""Add or remove Bessel's correction."""
      # Removes Bessel's correction if remove == True, adds it otherwise.
      # This is to be consistent with non-fused batch norm. Note that the
      # variance computed by fused batch norm is with Bessel's correction.
      # This is only used in legacy V1 batch norm tests.
      if self._bessels_correction_test_only:
        return variance
      sample_size = tf.cast(
          tf.compat.v1.size(inputs) / tf.compat.v1.size(variance), variance.dtype)
      if remove:
        factor = (sample_size -
                  tf.cast(1.0, variance.dtype)) / sample_size
      else:
        factor = sample_size / (
            sample_size - tf.cast(1.0, variance.dtype))
      return variance * factor

    def _fused_batch_norm_training():
      return tf.compat.v1.nn.fused_batch_norm(
          inputs,
          gamma,
          beta,
          mean=self.moving_mean,
          variance=_maybe_add_or_remove_bessels_correction(
              self.moving_variance, remove=False),
          epsilon=self.epsilon,
          is_training=True,
          data_format=self._data_format,
          exponential_avg_factor=exponential_avg_factor)

    def _fused_batch_norm_training_empty():
      return inputs, self.moving_mean, self.moving_variance

    def _fused_batch_norm_inference():
      return tf.compat.v1.nn.fused_batch_norm(
          inputs,
          gamma,
          beta,
          mean=self.moving_mean,
          variance=self.moving_variance,
          epsilon=self.epsilon,
          is_training=False,
          data_format=self._data_format)

    train_op = _fused_batch_norm_training
    if use_fused_avg_updates and input_batch_size is not None:
      # pylint: disable=g-long-lambda
      train_op = lambda: control_flow_util.smart_cond(
          input_batch_size > 0, _fused_batch_norm_training,
          _fused_batch_norm_training_empty)
      # pylint: enable=g-long-lambda

    output, mean, variance = control_flow_util.smart_cond(
        training, train_op, _fused_batch_norm_inference)
    variance = _maybe_add_or_remove_bessels_correction(variance, remove=True)

    training_value = control_flow_util.constant_value(training)
    if training_value or training_value is None:
      if not use_fused_avg_updates:
        if training_value is None:
          momentum = control_flow_util.smart_cond(training,
                                                  lambda: self.momentum,
                                                  lambda: 1.0)
        else:
          momentum = tf.convert_to_tensor(self.momentum)

      def mean_update():
        """Update self.moving_mean with the most recent data point."""
        if use_fused_avg_updates:
          return self._assign_new_value(self.moving_mean, mean)
        else:
          return self._assign_moving_average(self.moving_mean, mean, momentum,
                                             input_batch_size)

      def variance_update():
        """Update self.moving_variance with the most recent data point."""
        if use_fused_avg_updates:
          return self._assign_new_value(self.moving_variance, variance)
        else:
          return self._assign_moving_average(self.moving_variance, variance,
                                             momentum, input_batch_size)

      self.add_update(mean_update)
      self.add_update(variance_update)

    return output

  def _renorm_correction_and_moments(self, mean, variance, training,
                                     inputs_size):
    """Returns the correction and update values for renorm."""
    stddev = tf.sqrt(variance + self.epsilon)
    # Compute the average mean and standard deviation, as if they were
    # initialized with this batch's moments.
    renorm_mean = self.renorm_mean
    # Avoid divide by zero early on in training.
    renorm_stddev = tf.maximum(self.renorm_stddev,
                                     tf.sqrt(self.epsilon))
    # Compute the corrections for batch renorm.
    r = stddev / renorm_stddev
    d = (mean - renorm_mean) / renorm_stddev
    # Ensure the corrections use pre-update moving averages.
    with tf.control_dependencies([r, d]):
      mean = tf.identity(mean)
      stddev = tf.identity(stddev)
    rmin, rmax, dmax = [
        self.renorm_clipping.get(key) for key in ['rmin', 'rmax', 'dmax']
    ]
    if rmin is not None:
      r = tf.maximum(r, rmin)
    if rmax is not None:
      r = tf.minimum(r, rmax)
    if dmax is not None:
      d = tf.maximum(d, -dmax)
      d = tf.minimum(d, dmax)
    # When not training, use r=1, d=0.
    r = control_flow_util.smart_cond(training, lambda: r,
                                     lambda: tf.compat.v1.ones_like(r))
    d = control_flow_util.smart_cond(training, lambda: d,
                                     lambda: tf.compat.v1.zeros_like(d))

    def _update_renorm_variable(var, value, inputs_size):
      """Updates a moving average and weight, returns the unbiased value."""
      value = tf.identity(value)

      def _do_update():
        """Updates the var, returns the updated value."""
        new_var = self._assign_moving_average(var, value, self.renorm_momentum,
                                              inputs_size)
        return new_var

      def _fake_update():
        return tf.identity(var)

      return control_flow_util.smart_cond(training, _do_update, _fake_update)

    # TODO(yuefengz): colocate the operations
    update_new_mean = _update_renorm_variable(self.renorm_mean, mean,
                                              inputs_size)
    update_new_stddev = _update_renorm_variable(self.renorm_stddev, stddev,
                                                inputs_size)

    # Update the inference mode moving averages with the batch value.
    with tf.control_dependencies([update_new_mean, update_new_stddev]):
      out_mean = tf.identity(mean)
      out_variance = tf.identity(variance)

    return (r, d, out_mean, out_variance)

  def _calculate_mean_and_var(self, inputs, reduction_axes, keep_dims):
    return tf.compat.v1.nn.moments(inputs, reduction_axes, keep_dims=keep_dims)

  def _moments(self, inputs, reduction_axes, keep_dims):
    mean, variance = self._calculate_mean_and_var(inputs, reduction_axes,
                                                  keep_dims)
    # TODO(b/129279393): Support zero batch input in non DistributionStrategy
    # code as well.
    if self._support_zero_size_input():
      input_batch_size = tf.compat.v1.shape(inputs)[0]
      mean = tf.compat.v1.where(
          input_batch_size > 0, mean, backend.zeros_like(mean))
      variance = tf.compat.v1.where(input_batch_size > 0, variance,
                                 backend.zeros_like(variance))
    return mean, variance

  def _get_training_value(self, training=None):
    if training is None:
      training = backend.learning_phase()
    if self._USE_V2_BEHAVIOR:
      if isinstance(training, int):
        training = bool(training)
      if not self.trainable:
        # When the layer is not trainable, it overrides the value passed from
        # model.
        training = False
    return training

  def call(self, inputs, training=None):
    training = self._get_training_value(training)

    if self.virtual_batch_size is not None:
      # Virtual batches (aka ghost batches) can be simulated by reshaping the
      # Tensor and reusing the existing batch norm implementation
      original_shape = tf.compat.v1.shape(inputs)
      original_shape = tf.concat(
          [tf.constant([-1]), original_shape[1:]], axis=0)
      expanded_shape = tf.concat([
          tf.constant([self.virtual_batch_size, -1]),
          original_shape[1:]
      ],
                                        axis=0)

      # Will cause errors if virtual_batch_size does not divide the batch size
      inputs = tf.reshape(inputs, expanded_shape)

      def undo_virtual_batching(outputs):
        outputs = tf.reshape(outputs, original_shape)
        return outputs

    if self.fused:
      outputs = self._fused_batch_norm(inputs, training=training)
      if self.virtual_batch_size is not None:
        # Currently never reaches here since fused_batch_norm does not support
        # virtual batching
        outputs = undo_virtual_batching(outputs)
      return outputs

    inputs_dtype = inputs.dtype.base_dtype
    if inputs_dtype in (tf.float16, tf.bfloat16):
      # Do all math in float32 if given 16-bit inputs for numeric stability.
      # In particular, it's very easy for variance to overflow in float16 and
      # for safety we also choose to cast bfloat16 to float32.
      inputs = tf.cast(inputs, tf.float32)

    # Compute the axes along which to reduce the mean / variance
    input_shape = inputs.shape
    ndims = len(input_shape)
    reduction_axes = [i for i in range(ndims) if i not in self.axis]
    if self.virtual_batch_size is not None:
      del reduction_axes[1]  # Do not reduce along virtual batch dim

    # Broadcasting only necessary for single-axis batch norm where the axis is
    # not the last dimension
    broadcast_shape = [1] * ndims
    broadcast_shape[self.axis[0]] = input_shape.dims[self.axis[0]].value

    def _broadcast(v):
      if (v is not None and len(v.shape) != ndims and
          reduction_axes != list(range(ndims - 1))):
        return tf.reshape(v, broadcast_shape)
      return v

    scale, offset = _broadcast(self.gamma), _broadcast(self.beta)

    def _compose_transforms(scale, offset, then_scale, then_offset):
      if then_scale is not None:
        scale *= then_scale
        offset *= then_scale
      if then_offset is not None:
        offset += then_offset
      return (scale, offset)

    # Determine a boolean value for `training`: could be True, False, or None.
    training_value = control_flow_util.constant_value(training)
    if training_value == False:  # pylint: disable=singleton-comparison,g-explicit-bool-comparison
      mean, variance = self.moving_mean, self.moving_variance
    else:
      if self.adjustment:
        adj_scale, adj_bias = self.adjustment(tf.compat.v1.shape(inputs))
        # Adjust only during training.
        adj_scale = control_flow_util.smart_cond(
            training, lambda: adj_scale, lambda: tf.compat.v1.ones_like(adj_scale))
        adj_bias = control_flow_util.smart_cond(
            training, lambda: adj_bias, lambda: tf.compat.v1.zeros_like(adj_bias))
        scale, offset = _compose_transforms(adj_scale, adj_bias, scale, offset)

      # Some of the computations here are not necessary when training==False
      # but not a constant. However, this makes the code simpler.
      keep_dims = self.virtual_batch_size is not None or len(self.axis) > 1
      mean, variance = self._moments(
          tf.cast(inputs, self._param_dtype),
          reduction_axes,
          keep_dims=keep_dims)

      moving_mean = self.moving_mean
      moving_variance = self.moving_variance

      mean = control_flow_util.smart_cond(
          training, lambda: mean,
          lambda: tf.convert_to_tensor(moving_mean))
      variance = control_flow_util.smart_cond(
          training, lambda: variance,
          lambda: tf.convert_to_tensor(moving_variance))

      if self.virtual_batch_size is not None:
        # This isn't strictly correct since in ghost batch norm, you are
        # supposed to sequentially update the moving_mean and moving_variance
        # with each sub-batch. However, since the moving statistics are only
        # used during evaluation, it is more efficient to just update in one
        # step and should not make a significant difference in the result.
        new_mean = tf.reduce_mean(mean, axis=1, keepdims=True)
        new_variance = tf.reduce_mean(variance, axis=1, keepdims=True)
      else:
        new_mean, new_variance = mean, variance

      if self._support_zero_size_input():
        # Keras assumes that batch dimension is the first dimension for Batch
        # Normalization.
        input_batch_size = tf.compat.v1.shape(inputs)[0]
      else:
        input_batch_size = None

      if self.renorm:
        r, d, new_mean, new_variance = self._renorm_correction_and_moments(
            new_mean, new_variance, training, input_batch_size)
        # When training, the normalized values (say, x) will be transformed as
        # x * gamma + beta without renorm, and (x * r + d) * gamma + beta
        # = x * (r * gamma) + (d * gamma + beta) with renorm.
        r = _broadcast(tf.stop_gradient(r, name='renorm_r'))
        d = _broadcast(tf.stop_gradient(d, name='renorm_d'))
        scale, offset = _compose_transforms(r, d, scale, offset)

      def _do_update(var, value):
        """Compute the updates for mean and variance."""
        return self._assign_moving_average(var, value, self.momentum,
                                           input_batch_size)

      def mean_update():
        true_branch = lambda: _do_update(self.moving_mean, new_mean)
        false_branch = lambda: self.moving_mean
        return control_flow_util.smart_cond(training, true_branch, false_branch)

      def variance_update():
        """Update the moving variance."""

        def true_branch_renorm():
          # We apply epsilon as part of the moving_stddev to mirror the training
          # code path.
          moving_stddev = _do_update(self.moving_stddev,
                                     tf.sqrt(new_variance + self.epsilon))
          return self._assign_new_value(
              self.moving_variance,
              # Apply relu in case floating point rounding causes it to go
              # negative.
              backend.relu(moving_stddev * moving_stddev - self.epsilon))

        if self.renorm:
          true_branch = true_branch_renorm
        else:
          true_branch = lambda: _do_update(self.moving_variance, new_variance)

        false_branch = lambda: self.moving_variance
        return control_flow_util.smart_cond(training, true_branch, false_branch)

      self.add_update(mean_update)
      self.add_update(variance_update)

    mean = tf.cast(mean, inputs.dtype)
    variance = tf.cast(variance, inputs.dtype)
    if offset is not None:
      offset = tf.cast(offset, inputs.dtype)
    if scale is not None:
      scale = tf.cast(scale, inputs.dtype)
    outputs = tf.nn.batch_normalization(inputs, _broadcast(mean),
                                     _broadcast(variance), offset, scale,
                                     self.epsilon)
    if inputs_dtype in (tf.float16, tf.bfloat16):
      outputs = tf.cast(outputs, inputs_dtype)

    # If some components of the shape got lost due to adjustments, fix that.
    outputs.set_shape(input_shape)

    if self.virtual_batch_size is not None:
      outputs = undo_virtual_batching(outputs)
    return outputs

  def compute_output_shape(self, input_shape):
    return input_shape

  def get_config(self):
    config = {
        'axis':
            self.axis,
        'momentum':
            self.momentum,
        'epsilon':
            self.epsilon,
        'center':
            self.center,
        'scale':
            self.scale,
        'beta_initializer':
            initializers.serialize(self.beta_initializer),
        'gamma_initializer':
            initializers.serialize(self.gamma_initializer),
        'moving_mean_initializer':
            initializers.serialize(self.moving_mean_initializer),
        'moving_variance_initializer':
            initializers.serialize(self.moving_variance_initializer),
        'beta_regularizer':
            regularizers.serialize(self.beta_regularizer),
        'gamma_regularizer':
            regularizers.serialize(self.gamma_regularizer),
        'beta_constraint':
            constraints.serialize(self.beta_constraint),
        'gamma_constraint':
            constraints.serialize(self.gamma_constraint)
    }
    # Only add TensorFlow-specific parameters if they are set, so as to preserve
    # model compatibility with external Keras.
    if self.renorm:
      config['renorm'] = True
      config['renorm_clipping'] = self.renorm_clipping
      config['renorm_momentum'] = self.renorm_momentum
    if self.virtual_batch_size is not None:
      config['virtual_batch_size'] = self.virtual_batch_size
    # Note: adjustment is not serializable.
    if self.adjustment is not None:
      logging.warning('The `adjustment` function of this `BatchNormalization` '
                      'layer cannot be serialized and has been omitted from '
                      'the layer config. It will not be included when '
                      're-creating the layer from the saved config.')
    base_config = super(BatchNormalizationBase, self).get_config()
    return dict(list(base_config.items()) + list(config.items()))


# pylint: disable=missing-docstring
@keras_export(v1=['keras.layers.BatchNormalization'])
class BatchNormalization(BatchNormalizationBase):
  _USE_V2_BEHAVIOR = False


@keras_export('keras.layers.LayerNormalization')
class LayerNormalization(Layer):
  """Layer normalization layer (Ba et al., 2016).

  Normalize the activations of the previous layer for each given example in a
  batch independently, rather than across a batch like Batch Normalization.
  i.e. applies a transformation that maintains the mean activation within each
  example close to 0 and the activation standard deviation close to 1.

  Given a tensor `inputs`, moments are calculated and normalization
  is performed across the axes specified in `axis`.

  Example:

  >>> data = tf.constant(np.arange(10).reshape(5, 2) * 10, dtype=tf.float32)
  >>> print(data)
  tf.Tensor(
  [[ 0. 10.]
   [20. 30.]
   [40. 50.]
   [60. 70.]
   [80. 90.]], shape=(5, 2), dtype=float32)

  >>> layer = tf.keras.layers.LayerNormalization(axis=1)
  >>> output = layer(data)
  >>> print(output)
  tf.Tensor(
  [[-1. 1.]
   [-1. 1.]
   [-1. 1.]
   [-1. 1.]
   [-1. 1.]], shape=(5, 2), dtype=float32)

  Notice that with Layer Normalization the normalization happens across the
  axes *within* each example, rather than across different examples in the
  batch.

  If `scale` or `center` are enabled, the layer will scale the normalized
  outputs by broadcasting them with a trainable variable `gamma`, and center
  the outputs by broadcasting with a trainable variable `beta`. `gamma` will
  default to a ones tensor and `beta` will default to a zeros tensor, so that
  centering and scaling are no-ops before training has begun.

  So, with scaling and centering enabled the normalization equations
  are as follows:

  Let the intermediate activations for a mini-batch to be the `inputs`.

  For each sample `x_i` in `inputs` with `k` features, we compute the mean and
  variance of the sample:

  ```python
  mean_i = sum(x_i[j] for j in range(k)) / k
  var_i = sum((x_i[j] - mean_i) ** 2 for j in range(k)) / k
  ```

  and then compute a normalized `x_i_normalized`, including a small factor
  `epsilon` for numerical stability.

  ```python
  x_i_normalized = (x_i - mean_i) / sqrt(var_i + epsilon)
  ```

  And finally `x_i_normalized ` is linearly transformed by `gamma` and `beta`,
  which are learned parameters:

  ```python
  output_i = x_i_normalized * gamma + beta
  ```

  `gamma` and `beta` will span the axes of `inputs` specified in `axis`, and
  this part of the inputs' shape must be fully defined.

  For example:

  >>> layer = tf.keras.layers.LayerNormalization(axis=[1, 2, 3])
  >>> layer.build([5, 20, 30, 40])
  >>> print(layer.beta.shape)
  (20, 30, 40)
  >>> print(layer.gamma.shape)
  (20, 30, 40)

  Note that other implementations of layer normalization may choose to define
  `gamma` and `beta` over a separate set of axes from the axes being
  normalized across. For example, Group Normalization
  ([Wu et al. 2018](https://arxiv.org/abs/1803.08494)) with group size of 1
  corresponds to a Layer Normalization that normalizes across height, width,
  and channel and has `gamma` and `beta` span only the channel dimension.
  So, this Layer Normalization implementation will not match a Group
  Normalization layer with group size set to 1.

  Args:
    axis: Integer or List/Tuple. The axis or axes to normalize across. Typically
      this is the features axis/axes. The left-out axes are typically the batch
      axis/axes. This argument defaults to `-1`, the last dimension in the
      input.
    epsilon: Small float added to variance to avoid dividing by zero. Defaults
      to 1e-3
    center: If True, add offset of `beta` to normalized tensor. If False, `beta`
      is ignored. Defaults to True.
    scale: If True, multiply by `gamma`. If False, `gamma` is not used. Defaults
      to True. When the next layer is linear (also e.g. `nn.relu`), this can be
      disabled since the scaling will be done by the next layer.
    beta_initializer: Initializer for the beta weight. Defaults to zeros.
    gamma_initializer: Initializer for the gamma weight. Defaults to ones.
    beta_regularizer: Optional regularizer for the beta weight. None by default.
    gamma_regularizer: Optional regularizer for the gamma weight. None by
      default.
    beta_constraint: Optional constraint for the beta weight. None by default.
    gamma_constraint: Optional constraint for the gamma weight. None by default.

  Input shape:
    Arbitrary. Use the keyword argument `input_shape` (tuple of
    integers, does not include the samples axis) when using this layer as the
    first layer in a model.

  Output shape:
    Same shape as input.

  Reference:
    - [Lei Ba et al., 2016](https://arxiv.org/abs/1607.06450).
  """

  def __init__(self,
               axis=-1,
               epsilon=1e-3,
               center=True,
               scale=True,
               beta_initializer='zeros',
               gamma_initializer='ones',
               beta_regularizer=None,
               gamma_regularizer=None,
               beta_constraint=None,
               gamma_constraint=None,
               **kwargs):
    super(LayerNormalization, self).__init__(**kwargs)
    if isinstance(axis, (list, tuple)):
      self.axis = axis[:]
    elif isinstance(axis, int):
      self.axis = axis
    else:
      raise TypeError('Expected an int or a list/tuple of ints for the '
                      'argument \'axis\', but received: %r' % axis)

    self.epsilon = epsilon
    self.center = center
    self.scale = scale
    self.beta_initializer = initializers.get(beta_initializer)
    self.gamma_initializer = initializers.get(gamma_initializer)
    self.beta_regularizer = regularizers.get(beta_regularizer)
    self.gamma_regularizer = regularizers.get(gamma_regularizer)
    self.beta_constraint = constraints.get(beta_constraint)
    self.gamma_constraint = constraints.get(gamma_constraint)

    self.supports_masking = True

    # Indicates whether a faster fused implementation can be used. This will be
    # set to True or False in build()"
    self._fused = None

  def _fused_can_be_used(self, ndims):
    """Return false if fused implementation cannot be used.

    Check if the axis is contiguous and can be collapsed into the last axis.
    The self.axis is assumed to have no duplicates.
    """
    axis = sorted(self.axis)
    can_use_fused = False

    if axis[-1] == ndims - 1 and axis[-1] - axis[0] == len(axis) - 1:
      can_use_fused = True

    # fused_batch_norm will silently raise epsilon to be at least 1.001e-5, so
    # we cannot used the fused version if epsilon is below that value. Also, the
    # variable dtype must be float32, as fused_batch_norm only supports float32
    # variables.
    if self.epsilon < 1.001e-5 or self.dtype != 'float32':
      can_use_fused = False

    return can_use_fused

  def build(self, input_shape):
    ndims = len(input_shape)
    if ndims is None:
      raise ValueError('Input shape %s has undefined rank.' % input_shape)

    # Convert axis to list and resolve negatives
    if isinstance(self.axis, int):
      self.axis = [self.axis]
    elif isinstance(self.axis, tuple):
      self.axis = list(self.axis)
    for idx, x in enumerate(self.axis):
      if x < 0:
        self.axis[idx] = ndims + x

    # Validate axes
    for x in self.axis:
      if x < 0 or x >= ndims:
        raise ValueError('Invalid axis: %d' % x)
    if len(self.axis) != len(set(self.axis)):
      raise ValueError('Duplicate axis: {}'.format(tuple(self.axis)))

    param_shape = [input_shape[dim] for dim in self.axis]
    if self.scale:
      self.gamma = self.add_weight(
          name='gamma',
          shape=param_shape,
          initializer=self.gamma_initializer,
          regularizer=self.gamma_regularizer,
          constraint=self.gamma_constraint,
          trainable=True,
          experimental_autocast=False)
    else:
      self.gamma = None

    if self.center:
      self.beta = self.add_weight(
          name='beta',
          shape=param_shape,
          initializer=self.beta_initializer,
          regularizer=self.beta_regularizer,
          constraint=self.beta_constraint,
          trainable=True,
          experimental_autocast=False)
    else:
      self.beta = None

    self._fused = self._fused_can_be_used(ndims)

    self.built = True

  def call(self, inputs):
    # Compute the axes along which to reduce the mean / variance
    input_shape = inputs.shape
    ndims = len(input_shape)

    # Broadcasting only necessary for norm when the axis is not just
    # the last dimension
    broadcast_shape = [1] * ndims
    for dim in self.axis:
      broadcast_shape[dim] = input_shape.dims[dim].value

    def _broadcast(v):
      if (v is not None and len(v.shape) != ndims and self.axis != [ndims - 1]):
        return tf.reshape(v, broadcast_shape)
      return v

    if not self._fused:
      input_dtype = inputs.dtype
      if input_dtype in ('float16', 'bfloat16') and self.dtype == 'float32':
        # If mixed precision is used, cast inputs to float32 so that this is at
        # least as numerically stable as the fused version.
        inputs = tf.cast(inputs, 'float32')

      # Calculate the moments on the last axis (layer activations).
      mean, variance = tf.compat.v1.nn.moments(inputs, self.axis, keep_dims=True)

      scale, offset = _broadcast(self.gamma), _broadcast(self.beta)

      # Compute layer normalization using the batch_normalization function.
      outputs = tf.nn.batch_normalization(
          inputs,
          mean,
          variance,
          offset=offset,
          scale=scale,
          variance_epsilon=self.epsilon)
      outputs = tf.cast(outputs, input_dtype)
    else:
      # Collapse dims before self.axis, and dims in self.axis
      pre_dim, in_dim = (1, 1)
      axis = sorted(self.axis)
      tensor_shape = tf.compat.v1.shape(inputs)
      for dim in range(0, ndims):
        dim_tensor = tensor_shape[dim]
        if dim < axis[0]:
          pre_dim = pre_dim * dim_tensor
        else:
          assert dim in axis
          in_dim = in_dim * dim_tensor

      squeezed_shape = [1, pre_dim, in_dim, 1]
      # This fused operation requires reshaped inputs to be NCHW.
      data_format = 'NCHW'

      inputs = tf.reshape(inputs, squeezed_shape)

      # self.gamma and self.beta have the wrong shape for fused_batch_norm, so
      # we cannot pass them as the scale and offset parameters. Therefore, we
      # create two constant tensors in correct shapes for fused_batch_norm and
      # later construct a separate calculation on the scale and offset.
      scale = tf.ones([pre_dim], dtype=self.dtype)
      offset = tf.zeros([pre_dim], dtype=self.dtype)

      # Compute layer normalization using the fused_batch_norm function.
      outputs, _, _ = tf.compat.v1.nn.fused_batch_norm(
          inputs,
          scale=scale,
          offset=offset,
          epsilon=self.epsilon,
          data_format=data_format)

      outputs = tf.reshape(outputs, tensor_shape)

      scale, offset = _broadcast(self.gamma), _broadcast(self.beta)

      if scale is not None:
        outputs = outputs * tf.cast(scale, outputs.dtype)
      if offset is not None:
        outputs = outputs + tf.cast(offset, outputs.dtype)

    # If some components of the shape got lost due to adjustments, fix that.
    outputs.set_shape(input_shape)

    return outputs

  def compute_output_shape(self, input_shape):
    return input_shape

  def get_config(self):
    config = {
        'axis': self.axis,
        'epsilon': self.epsilon,
        'center': self.center,
        'scale': self.scale,
        'beta_initializer': initializers.serialize(self.beta_initializer),
        'gamma_initializer': initializers.serialize(self.gamma_initializer),
        'beta_regularizer': regularizers.serialize(self.beta_regularizer),
        'gamma_regularizer': regularizers.serialize(self.gamma_regularizer),
        'beta_constraint': constraints.serialize(self.beta_constraint),
        'gamma_constraint': constraints.serialize(self.gamma_constraint)
    }
    base_config = super(LayerNormalization, self).get_config()
    return dict(list(base_config.items()) + list(config.items()))
