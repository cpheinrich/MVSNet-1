#!/usr/bin/env python

from __future__ import print_function
from loss import *
from model import *
from preprocess import *
from tools.common import Notify
from mvs_data_generation.cluster_generator import ClusterGenerator
"""
Copyright 2019, Yao Yao, HKUST.
Test script.
"""


import os
import time
import sys
import math
import argparse
import numpy as np
import imageio

import cv2
import tensorflow as tf

sys.path.append("../")

# dataset parameters
tf.app.flags.DEFINE_string('dense_folder', None,
                           """Root path to dense folder.""")
tf.app.flags.DEFINE_string('model_dir',
                           '../model',
                           """Path to restore the model.""")
tf.app.flags.DEFINE_integer('ckpt_step', 100000,
                            """ckpt step.""")

# input parameters
tf.app.flags.DEFINE_integer('view_num', 5,
                            """Number of images (1 ref image and view_num - 1 view images).""")
tf.app.flags.DEFINE_integer('max_d', 200,
                            """Maximum depth step when testing.""")
tf.app.flags.DEFINE_integer('max_w', 1024,
                            """Maximum image width when testing.""")
tf.app.flags.DEFINE_integer('max_h', 768,
                            """Maximum image height when testing.""")
tf.app.flags.DEFINE_float('sample_scale', 0.25,
                          """Downsample scale for building cost volume (W and H).""")
tf.app.flags.DEFINE_float('interval_scale', 1.0,
                          """Downsample scale for building cost volume (D).""")
tf.app.flags.DEFINE_float('base_image_size', 8,
                          """Base image size""")
tf.app.flags.DEFINE_integer('batch_size', 1,
                            """Testing batch size.""")
tf.app.flags.DEFINE_bool('adaptive_scaling', True,
                         """Let image size to fit the network, including 'scaling', 'cropping'""")

# network architecture
tf.app.flags.DEFINE_string('regularization', 'GRU',
                           """Regularization method, including '3DCNNs' and 'GRU'""")
tf.app.flags.DEFINE_boolean('refinement', False,
                            """Whether to apply depth map refinement for MVSNet""")
tf.app.flags.DEFINE_bool('inverse_depth', True,
                         """Whether to apply inverse depth for R-MVSNet""")
tf.app.flags.DEFINE_boolean('external_data_gen', True,
                            """Whether or not to use the new external data gen""")

FLAGS = tf.app.flags.FLAGS


class MVSGenerator:
    """ data generator class, tf only accept generator without param """

    def __init__(self, sample_list, view_num):
        self.sample_list = sample_list
        self.view_num = view_num
        self.sample_num = len(sample_list)
        self.counter = 0

    def __iter__(self):
        while True:
            target_frame_index = 0
            for data in self.sample_list:

                # read input data
                images = []
                cams = []
                image_index = int(os.path.splitext(
                    os.path.basename(data[0]))[0])
                print("Image index is: ", image_index)
                selected_view_num = int(len(data) / 2)
                pose_file_path = os.path.join(FLAGS.dense_folder, 'poses.txt')

                for view in range(min(self.view_num, selected_view_num)):
                    image_file = file_io.FileIO(data[2 * view], mode='r')
                    image = scipy.misc.imread(image_file, mode='RGB')
                    image = cv2.cvtColor(image, cv2.COLOR_RGB2BGR)
                    cam_file = file_io.FileIO(data[2 * view + 1], mode='r')
                    cam = load_cam(cam_file, FLAGS.interval_scale)
                    if cam[1][3][2] == 0:
                        cam[1][3][2] = FLAGS.max_d
                    images.append(image)
                    cams.append(cam)
                    if view == 0:
                        with open(pose_file_path, 'a') as f:
                            f.write(pose_string(cam))

                if selected_view_num < self.view_num:
                    for view in range(selected_view_num, self.view_num):
                        image_file = file_io.FileIO(data[0], mode='r')
                        image = scipy.misc.imread(image_file, mode='RGB')
                        image = cv2.cvtColor(image, cv2.COLOR_RGB2BGR)
                        cam_file = file_io.FileIO(data[1], mode='r')
                        cam = load_cam(cam_file, FLAGS.interval_scale)
                        images.append(image)
                        cams.append(cam)
                print('range: ', cams[0][1, 3, 0], cams[0]
                      [1, 3, 1], cams[0][1, 3, 2], cams[0][1, 3, 3])

                # determine a proper scale to resize input
                resize_scale = 1
                if FLAGS.adaptive_scaling:
                    h_scale = 0
                    w_scale = 0
                    for view in range(self.view_num):
                        height_scale = float(FLAGS.max_h) / \
                            images[view].shape[0]
                        width_scale = float(FLAGS.max_w) / \
                            images[view].shape[1]
                        if height_scale > h_scale:
                            h_scale = height_scale
                        if width_scale > w_scale:
                            w_scale = width_scale
                    if h_scale > 1 or w_scale > 1:
                        print("max_h, max_w should < W and H!")
                        exit(-1)
                    resize_scale = h_scale
                    if w_scale > h_scale:
                        resize_scale = w_scale
                scaled_input_images, scaled_input_cams = scale_mvs_input(
                    images, cams, scale=resize_scale)

                # crop to fit network
                croped_images, croped_cams = crop_mvs_input(
                    scaled_input_images, scaled_input_cams)

                # center images
                centered_images = []
                for view in range(self.view_num):
                    centered_images.append(center_image(croped_images[view]))

                # sample cameras for building cost volume
                real_cams = np.copy(croped_cams)
                scaled_cams = scale_mvs_camera(
                    croped_cams, scale=FLAGS.sample_scale)

                # return mvs input
                scaled_images = []
                for view in range(self.view_num):
                    scaled_images.append(scale_image(
                        croped_images[view], scale=FLAGS.sample_scale))
                scaled_images = np.stack(scaled_images, axis=0)
                croped_images = np.stack(croped_images, axis=0)
                scaled_cams = np.stack(scaled_cams, axis=0)
                print('--scaled image shape', scaled_images.shape)
                print('--scaled cams shape', scaled_cams.shape)
                print('--centered image shape', centered_images[0].shape)

                image_path = os.path.join(
                    FLAGS.dense_folder, 'centered_images', '{}.jpg'.format(image_index))
                imageio.imsave(image_path, centered_images[0].astype(np.uint8))

                self.counter += 1
                yield (scaled_images, centered_images, scaled_cams, image_index)


def mvsnet_pipeline(mvs_list=None):
    """ mvsnet in altizure pipeline """

    # create output folder
    output_folder = os.path.join(FLAGS.dense_folder, 'depths_mvsnet')
    if not os.path.isdir(output_folder):
        os.mkdir(output_folder)

    # testing set
    if FLAGS.external_data_gen:
        data_gen = ClusterGenerator(FLAGS.dense_folder, FLAGS.view_num, FLAGS.max_w, FLAGS.max_h,
                                    FLAGS.max_d, FLAGS.interval_scale, FLAGS.base_image_size, mode='test')
        mvs_generator = iter(data_gen)
        sample_size = len(data_gen.train_clusters)

    else:
        mvs_generator = iter(MVSGenerator(mvs_list, FLAGS.view_num))
        sample_size = len(mvs_list)
    generator_data_type = (tf.float32, tf.float32, tf.float32, tf.int32)
    mvs_set = tf.data.Dataset.from_generator(
        lambda: mvs_generator, generator_data_type)
    mvs_set = mvs_set.batch(FLAGS.batch_size)
    mvs_set = mvs_set.prefetch(buffer_size=1)

    # data from dataset via iterator
    mvs_iterator = mvs_set.make_initializable_iterator()
    scaled_images, centered_images, scaled_cams, image_index = mvs_iterator.get_next()

    # set shapes
    scaled_images.set_shape(tf.TensorShape(
        [None, FLAGS.view_num, None, None, 3]))
    centered_images.set_shape(tf.TensorShape(
        [None, FLAGS.view_num, None, None, 3]))
    scaled_cams.set_shape(tf.TensorShape([None, FLAGS.view_num, 2, 4, 4]))
    depth_start = tf.reshape(
        tf.slice(scaled_cams, [0, 0, 1, 3, 0], [FLAGS.batch_size, 1, 1, 1, 1]), [FLAGS.batch_size])
    depth_interval = tf.reshape(
        tf.slice(scaled_cams, [0, 0, 1, 3, 1], [FLAGS.batch_size, 1, 1, 1, 1]), [FLAGS.batch_size])
    depth_num = tf.cast(
        tf.reshape(tf.slice(scaled_cams, [0, 0, 1, 3, 2], [1, 1, 1, 1, 1]), []), 'int32')

    # deal with inverse depth
    if FLAGS.regularization == '3DCNNs' and FLAGS.inverse_depth:
        depth_end = tf.reshape(
            tf.slice(scaled_cams, [0, 0, 1, 3, 3], [FLAGS.batch_size, 1, 1, 1, 1]), [FLAGS.batch_size])
    else:
        depth_end = depth_start + \
            (tf.cast(depth_num, tf.float32) - 1) * depth_interval

    # depth map inference using 3DCNNs
    if FLAGS.regularization == '3DCNNs':
        init_depth_map, prob_map = inference_mem(
            centered_images, scaled_cams, FLAGS.max_d, depth_start, depth_interval)

        if FLAGS.refinement:
            ref_image = tf.squeeze(
                tf.slice(centered_images, [0, 0, 0, 0, 0], [-1, 1, -1, -1, 3]), axis=1)
            refined_depth_map = depth_refine(
                init_depth_map, ref_image, FLAGS.max_d, depth_start, depth_interval, True)

    # depth map inference using GRU
    elif FLAGS.regularization == 'GRU':
        init_depth_map, prob_map = inference_winner_take_all(centered_images, scaled_cams,
                                                             depth_num, depth_start, depth_end, reg_type='GRU', inverse_depth=FLAGS.inverse_depth)

    # init option
    init_op = tf.global_variables_initializer()
    var_init_op = tf.local_variables_initializer()

    # GPU grows incrementally
    config = tf.ConfigProto()
    config.gpu_options.allow_growth = True
    config.intra_op_parallelism_threads = 1

    with tf.Session(config=config) as sess:

        # initialization
        sess.run(var_init_op)
        sess.run(init_op)
        total_step = 0

        # load model
        if FLAGS.model_dir is not None:
            pretrained_model_ckpt_path = os.path.join(
                FLAGS.model_dir, FLAGS.regularization, 'model.ckpt')
            restorer = tf.train.Saver(tf.global_variables())
            restorer.restore(
                sess, '-'.join([pretrained_model_ckpt_path, str(FLAGS.ckpt_step)]))
            print(Notify.INFO, 'Pre-trained model restored from %s' %
                  ('-'.join([pretrained_model_ckpt_path, str(FLAGS.ckpt_step)])), Notify.ENDC)
            total_step = FLAGS.ckpt_step

        # run inference for each reference view
        sess.run(mvs_iterator.initializer)
        for step in range(sample_size):

            start_time = time.time()
            try:
                out_init_depth_map, out_prob_map, out_images, out_cams, out_index = sess.run(
                    [init_depth_map, prob_map, scaled_images, scaled_cams, image_index])
            except tf.errors.OutOfRangeError:
                print("all dense finished")  # ==> "End of dataset"
                break
            duration = time.time() - start_time
            print(Notify.INFO, 'depth inference %d finished. (%.3f sec/step)' % (step, duration),
                  Notify.ENDC)

            # squeeze output
            out_init_depth_image = np.squeeze(out_init_depth_map)
            out_prob_map = np.squeeze(out_prob_map)
            out_ref_image = np.squeeze(out_images)
            out_ref_image = np.squeeze(out_ref_image[0, :, :, :])
            out_ref_cam = np.squeeze(out_cams)
            out_ref_cam = np.squeeze(out_ref_cam[0, :, :, :])
            out_index = np.squeeze(out_index)

            # paths
            init_depth_map_path = output_folder + \
                ('/%08d_init.pfm' % out_index)
            prob_map_path = output_folder + ('/%08d_prob.pfm' % out_index)
            out_ref_image_path = output_folder + ('/%08d.jpg' % out_index)
            out_ref_cam_path = output_folder + ('/%08d.txt' % out_index)
            # png outputs
            prob_png = output_folder + ('/%08d_prob.png' % out_index)
            depth_png = output_folder + ('/%08d_depth.png' % out_index)

            # save output
            write_pfm(init_depth_map_path, out_init_depth_image)
            write_pfm(prob_map_path, out_prob_map)

            # for png outputs
            write_depth_map(depth_png, out_init_depth_image)
            write_confidence_map(prob_png, out_prob_map)

            out_ref_image = cv2.cvtColor(out_ref_image, cv2.COLOR_RGB2BGR)
            image_file = file_io.FileIO(out_ref_image_path, mode='w')
            scipy.misc.imsave(image_file, out_ref_image)
            write_cam(out_ref_cam_path, out_ref_cam)
            total_step += 1


def main(_):  # pylint: disable=unused-argument
    """ program entrance """
    # generate input path list
    if FLAGS.external_data_gen:
        mvsnet_pipeline()
    else:
        mvs_list = gen_pipeline_mvs_list(FLAGS.dense_folder)
        # mvsnet inference
        mvsnet_pipeline(mvs_list)


if __name__ == '__main__':
    tf.app.run()
