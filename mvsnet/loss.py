#!/usr/bin/env python
"""
Copyright 2019, Yao Yao, HKUST.
Model architectures.
"""

import sys
import math
import tensorflow as tf
import numpy as np

FLAGS = tf.app.flags.FLAGS


def masked_loss(y_true, y_pred, interval, alpha, beta):
    """ non zero mean absolute loss for one batch

    This function parameterizes a loss of the general form:

    Loss = N * (|y_true-y_pred| + epsilon(y_true))^alpha / y_true^beta

    where alpha and beta are scalars, and N is a normalization constant which depends on 
    alpha, beta and y_true. epsilon(y_true) is the expected noise of the measurement of y_true, and helps to prevent overfitting to noise
    in the depth map. 
    Additionally the numerator and denominator are multipled by a mask to mask out
    invalid pixels in the label. This was omitted above for notational simplicity.

    See this paper for a description and analysis of the noise model of the kinect sensor
    -- https://www.ncbi.nlm.nih.gov/pmc/articles/PMC3304120/

    One key takeaway is that the random error in Kinect depth maps increases quadratically with distance and 
    reaches a maximum of 4cm at the maximum range of 5 meters
     """

    with tf.name_scope('MAE'):
        shape = tf.shape(y_pred)
        interval = tf.reshape(interval, [shape[0]])
        mask_true = tf.cast(tf.not_equal(y_true, 0.0), dtype='float32')
        denominator = tf.abs(tf.reduce_sum(mask_true, axis=[1, 2, 3])) + 1e-6
        if beta != 1.0:
            denominator = tf.math.pow(denominator, beta)
        # Below we assume the random error in y_true increases linearly with distance
        # and that the error in y_true is 5mm at a distance of 1 meter
        epsilon = .005 * y_true
        numerator = tf.abs(y_true - y_pred) + epsilon
        if alpha != 1.0:
            numerator = tf.math.pow(
                numerator, alpha)
        # Apply the mask to the predicions and labels
        numerator = numerator*mask_true
        numerator = tf.reduce_sum(numerator, axis=[1, 2, 3])
        # The normalization is chosen so that, on average, the loss is of order 1
        normalization = tf.math.pow(
            tf.reduce_mean(denominator), beta - 1) / tf.math.pow(interval, alpha)
        loss = tf.reduce_sum(numerator / denominator) * normalization     # 1
    return loss


def less_one_percentage(y_true, y_pred, interval):
    """ less one accuracy for one batch """
    with tf.name_scope('less_one_error'):
        shape = tf.shape(y_pred)
        mask_true = tf.cast(tf.not_equal(y_true, 0.0), dtype='float32')
        denom = tf.abs(tf.reduce_sum(mask_true)) + 1e-6
        interval_image = tf.tile(tf.reshape(interval, [shape[0], 1, 1, 1]), [
            1, shape[1], shape[2], 1])
        abs_diff_image = tf.abs(y_true - y_pred) / interval_image
        less_one_image = mask_true * \
            tf.cast(tf.less_equal(abs_diff_image, 1.0), dtype='float32')
    return tf.reduce_sum(less_one_image) / denom


def less_three_percentage(y_true, y_pred, interval):
    """ less three accuracy for one batch """
    with tf.name_scope('less_three_error'):
        shape = tf.shape(y_pred)
        mask_true = tf.cast(tf.not_equal(y_true, 0.0), dtype='float32')
        denom = tf.abs(tf.reduce_sum(mask_true)) + 1e-6
        interval_image = tf.tile(tf.reshape(interval, [shape[0], 1, 1, 1]), [
            1, shape[1], shape[2], 1])
        abs_diff_image = tf.abs(y_true - y_pred) / interval_image
        less_three_image = mask_true * \
            tf.cast(tf.less_equal(abs_diff_image, 3.0), dtype='float32')
    return tf.reduce_sum(less_three_image) / denom


def mvsnet_regression_loss(estimated_depth_image, depth_image, depth_start, depth_end, alpha=1.0, beta=1.0):
    """ compute loss and accuracy """
    # For loss and accuracy we use a depth_interval that is independent of the number of depth buckets
    # so we can easily compare results for various depth_num. We divide by 191 for historical reasons.
    depth_interval = tf.div(depth_end-depth_start, 191.0)
    # non zero mean absulote loss

    loss = masked_loss(
        depth_image, estimated_depth_image, depth_interval, alpha, beta)
    # less one accuracy
    less_one_accuracy = less_one_percentage(
        depth_image, estimated_depth_image, depth_interval)
    # less three accuracy
    less_three_accuracy = less_three_percentage(
        depth_image, estimated_depth_image, depth_interval)

    return loss, less_one_accuracy, less_three_accuracy


def mvsnet_classification_loss(prob_volume, gt_depth_image, depth_num, depth_start, depth_interval):
    """ compute loss and accuracy """

    # get depth mask
    mask_true = tf.cast(tf.not_equal(gt_depth_image, 0.0), dtype='float32')
    valid_pixel_num = tf.reduce_sum(mask_true, axis=[1, 2, 3]) + 1e-7
    # gt depth map -> gt index map
    shape = tf.shape(gt_depth_image)
    depth_end = depth_start + \
        (tf.cast(depth_num, tf.float32) - 1) * depth_interval
    start_mat = tf.tile(tf.reshape(depth_start, [shape[0], 1, 1, 1]), [
        1, shape[1], shape[2], 1])

    interval_mat = tf.tile(tf.reshape(depth_interval, [shape[0], 1, 1, 1]), [
        1, shape[1], shape[2], 1])
    gt_index_image = tf.div(gt_depth_image - start_mat, interval_mat)
    gt_index_image = tf.multiply(mask_true, gt_index_image)
    gt_index_image = tf.cast(tf.round(gt_index_image), dtype='int32')
    # gt index map -> gt one hot volume (B x H x W x 1)
    gt_index_volume = tf.one_hot(gt_index_image, depth_num, axis=1)
    # cross entropy image (B x H x W x 1)
    cross_entropy_image = - \
        tf.reduce_sum(gt_index_volume * tf.log(prob_volume), axis=1)
    # masked cross entropy loss
    masked_cross_entropy_image = tf.multiply(mask_true, cross_entropy_image)
    masked_cross_entropy = tf.reduce_sum(
        masked_cross_entropy_image, axis=[1, 2, 3])
    masked_cross_entropy = tf.reduce_sum(
        masked_cross_entropy / valid_pixel_num)

    # winner-take-all depth map
    wta_index_map = tf.cast(tf.argmax(prob_volume, axis=1), dtype='float32')
    wta_depth_map = wta_index_map * interval_mat + start_mat

    # non zero mean absulote loss
    masked_mae = non_zero_mean_absolute_diff(
        gt_depth_image, wta_depth_map, tf.abs(depth_interval))
    # less one accuracy
    less_one_accuracy = less_one_percentage(
        gt_depth_image, wta_depth_map, tf.abs(depth_interval))
    # less three accuracy
    less_three_accuracy = less_three_percentage(
        gt_depth_image, wta_depth_map, tf.abs(depth_interval))

    return masked_cross_entropy, masked_mae, less_one_accuracy, less_three_accuracy, wta_depth_map
