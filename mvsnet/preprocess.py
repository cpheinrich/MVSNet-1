#!/usr/bin/env python
"""
Copyright 2019, Yao Yao, HKUST.
Training preprocesses.
"""

from __future__ import print_function

import os
import time
import glob
import random
import math
import re
import sys
import imageio

import cv2
import numpy as np
import tensorflow as tf
import scipy.io
import urllib
from tensorflow.python.lib.io import file_io
from mvsnet.utils import setup_logger
logger = setup_logger('image-processing')
FLAGS = tf.app.flags.FLAGS

def import_wandb_idempotent():
  if 'wandb' not in sys.modules:
    global wandb
    import wandb

def center_image(img):
    """ normalize image input """
    img = img.astype(np.float32)
    var = np.var(img, axis=(0,1), keepdims=True)
    mean = np.mean(img, axis=(0,1), keepdims=True)
    return (img - mean) / (np.sqrt(var) + 0.00000001)

def scale_camera(cam, scale=1):
    """ resize input in order to produce sampled depth map """
    new_cam = np.copy(cam)
    # focal: 
    new_cam[1][0][0] = cam[1][0][0] * scale
    new_cam[1][1][1] = cam[1][1][1] * scale
    # principle point:
    new_cam[1][0][2] = cam[1][0][2] * scale
    new_cam[1][1][2] = cam[1][1][2] * scale
    return new_cam

def scale_mvs_camera(cams, scale=1):
    """ resize input in order to produce sampled depth map """
    for view in range(FLAGS.view_num):
        cams[view] = scale_camera(cams[view], scale=scale)
    return cams

def scale_image(image, scale=1, interpolation='linear'):
    """ resize image using cv2 """
    if interpolation == 'linear':
        return cv2.resize(image, None, fx=scale, fy=scale, interpolation=cv2.INTER_LINEAR)
    if interpolation == 'nearest':
        return cv2.resize(image, None, fx=scale, fy=scale, interpolation=cv2.INTER_NEAREST)

def scale_mvs_input(images, cams, depth_image=None, scale=1):
    """ resize input to fit into the memory """
    for view in range(FLAGS.view_num):
        images[view] = scale_image(images[view], scale=scale)
        cams[view] = scale_camera(cams[view], scale=scale)

    if depth_image is None:
        return images, cams
    else:
        depth_image = scale_image(depth_image, scale=scale, interpolation='nearest')
        return images, cams, depth_image

def crop_mvs_input(images, cams, depth_image=None):
    """ resize images and cameras to fit the network (can be divided by base image size) """

    # crop images and cameras
    for view in range(FLAGS.view_num):
        h, w = images[view].shape[0:2]
        new_h = h
        new_w = w
        if new_h > FLAGS.height:
            new_h = FLAGS.height
        else:
            new_h = int(math.ceil(h / FLAGS.base_image_size) * FLAGS.base_image_size)
        if new_w > FLAGS.width:
            new_w = FLAGS.width
        else:
            new_w = int(math.ceil(w / FLAGS.base_image_size) * FLAGS.base_image_size)
        start_h = int(math.ceil((h - new_h) / 2))
        start_w = int(math.ceil((w - new_w) / 2))
        finish_h = start_h + new_h
        finish_w = start_w + new_w
        images[view] = images[view][start_h:finish_h, start_w:finish_w]
        cams[view][1][0][2] = cams[view][1][0][2] - start_w
        cams[view][1][1][2] = cams[view][1][1][2] - start_h

    # crop depth image
    if not depth_image is None:
        depth_image = depth_image[start_h:finish_h, start_w:finish_w]
        return images, cams, depth_image
    else:
        return images, cams

def mask_depth_image(depth_image, min_depth, max_depth):
    """ mask out-of-range pixel to zero """
    # print ('mask min max', min_depth, max_depth)
    ret, depth_image = cv2.threshold(depth_image, min_depth, 100000, cv2.THRESH_TOZERO)
    ret, depth_image = cv2.threshold(depth_image, max_depth, 100000, cv2.THRESH_TOZERO_INV)
    depth_image = np.expand_dims(depth_image, 2)
    return depth_image

def load_cam(file, interval_scale=1, max_d = None):
    """ read camera txt file """
    cam = np.zeros((2, 4, 4))
    words = file.read().split()
    # read extrinsic
    for i in range(0, 4):
        for j in range(0, 4):
            extrinsic_index = 4 * i + j + 1
            cam[0][i][j] = words[extrinsic_index]

    # read intrinsic
    for i in range(0, 3):
        for j in range(0, 3):
            intrinsic_index = 3 * i + j + 18
            cam[1][i][j] = words[intrinsic_index]
            
    if len(words) == 29:
        cam[1][3][0] = words[27]
        cam[1][3][1] = float(words[28]) * interval_scale
        if max_d is None:
            cam[1][3][2] = FLAGS.max_d
        else:
            cam[1][3][2] = max_d
        cam[1][3][3] = cam[1][3][0] + cam[1][3][1] * cam[1][3][2]
    elif len(words) == 30:
        cam[1][3][0] = words[27]
        cam[1][3][1] = float(words[28]) * interval_scale
        cam[1][3][2] = words[29]
        cam[1][3][3] = cam[1][3][0] + cam[1][3][1] * cam[1][3][2]
    elif len(words) == 31:
        cam[1][3][0] = words[27]
        cam[1][3][1] = float(words[28]) * interval_scale
        cam[1][3][2] = words[29]
        cam[1][3][3] = words[30]
    else:
        cam[1][3][0] = 0
        cam[1][3][1] = 0
        cam[1][3][2] = 0
        cam[1][3][3] = 0
    return cam

def load_cam_from_path(path, interval_scale = 1.0, max_d = None):
    return load_cam(open(path), interval_scale, max_d)

def pose_string(cam):
    row_0 = cam[0,0,:]
    row_1 = cam[0,1,:]
    row_2 = cam[0,2,:]
    pose_string = ''
    pose_string += str(row_0[0]) + ' '
    pose_string += str(row_0[1])+ ' '
    pose_string += str(row_0[2])+ ' '
    pose_string += str(row_0[3]/1000)+ ' '
    pose_string += str(row_1[0])+ ' '
    pose_string += str(row_1[1])+ ' '
    pose_string += str(row_1[2])+ ' '
    pose_string += str(row_1[3]/1000)+ ' '
    pose_string += str(row_2[0])+ ' '
    pose_string += str(row_2[1])+ ' '
    pose_string += str(row_2[2])+ ' '
    pose_string += str(row_2[3]/1000)
    pose_string += '\n'
    print(pose_string)
    return pose_string


def write_inverse_depth_map(image, file_path, exp=2):
    """ Writes an inverted depth map for visualization purposes
    args:
        image: Depth image to write
        file_path: Path where that image is
        exp: A positive number. Higher numbers lead to faster decay of brightness with depth"""
    
    max_int = 65535
    image = image.astype(np.float)
    # First normalize image so it has min 0 and max = max_int
    image -= image.min()
    image *= (max_int / image.max())
    # Invert the depth map and clip values
    inv_depth = np.power((max_int - image)/max_int,exp)*max_int
    inv_depth = np.clip(inv_depth, 0, max_int).astype(np.uint16)
    imageio.imsave(file_path, inv_depth)
    if FLAGS.wandb:
        import_wandb_idempotent()
        if 'unrefined' in file_path:
            wandb.log({"inverse_depths_unrefined": wandb.Image(
                (inv_depth.astype(np.float32)*(255.0/65535.0)), caption=file_path)})
        else:
            wandb.log({"inverse_depths": wandb.Image(
                (inv_depth.astype(np.float32)*(255.0/65535.0)), caption=file_path)})


def write_reference_image(image, file_path):
    """ Write the reference image to file """
    image = cv2.cvtColor(image, cv2.COLOR_RGB2BGR)
    image_file = file_io.FileIO(file_path, mode='w')
    scipy.misc.imsave(image_file, image)
    if FLAGS.wandb:
        import_wandb_idempotent()
        wandb.log({"reference_images": wandb.Image(
            image, caption=file_path)})

def write_residual_depth_map(image, file_path, exp=0.5):
    """ Writes an inverted depth map for visualization purposes
    args:
        image: Depth image to write
        file_path: Path where that image is
        exp: A positive number. Higher numbers lead to faster decay of brightness with depth"""

    max_int = 255
    image = image.astype(np.float)
    mean = image.mean()
    max_val = image.max()
    min_val = image.min()
    
    logger.debug('Residual max: {}'.format(max_val))
    logger.debug('Residual min: {}'.format(min_val))
    logger.debug('Residual mean: {}'.format(mean))
    abs_max = max(abs(max_val), abs(min_val))
    image *= (1.0/ abs_max)
    residual_plus = np.clip(image, 0, 1)
    residual_minus = -np.clip(image,-1, 0)
    residual_plus = np.power(residual_plus , exp)*max_int
    residual_minus = np.power(residual_minus, exp)*max_int
    residual_plus = residual_plus.astype(np.uint8)
    residual_minus = residual_minus.astype(np.uint8)
    residual_color = np.zeros((image.shape[0],image.shape[1],3))
    residual_color[:,:,0] = residual_plus
    residual_color[:,:,1] = residual_minus
    residual_color[:, :, 2] = (0.3*(residual_plus+residual_minus)).astype(np.uint8)
    imageio.imsave(file_path, residual_color)
    if FLAGS.wandb:
        import_wandb_idempotent()
        wandb.log({"residual_depths": wandb.Image(
            residual_color, caption=file_path)})


def write_depth_map(file_path, image, visualization = True):
    # convert to int and clip to range of [0, 2^16 -1]
    image = np.clip(image, 0, 65535).astype(np.uint16)
    imageio.imsave(file_path, image)
    if visualization:
        file_path_inverse = file_path.replace('.png', '_inverse.png')
        write_inverse_depth_map(image, file_path_inverse)

def write_confidence_map(file_path, image):
    # we convert probabilities in range [0,1] to ints in range [0, 2^16-1]
    if FLAGS.wandb:
        import_wandb_idempotent()
        wandb.log({"confidence_maps": wandb.Image(
            (image*255), caption=file_path)})
    scale_factor = 65535
    image *= scale_factor
    image = np.clip(image, 0, 65535).astype(np.uint16)
    imageio.imsave(file_path, image)


def write_cam(file, cam):
    # f = open(file, "w")
    f = file_io.FileIO(file, "w")

    f.write('extrinsic\n')
    for i in range(0, 4):
        for j in range(0, 4):
            f.write(str(cam[0][i][j]) + ' ')
        f.write('\n')
    f.write('\n')

    f.write('intrinsic\n')
    for i in range(0, 3):
        for j in range(0, 3):
            f.write(str(cam[1][i][j]) + ' ')
        f.write('\n')

    f.write('\n' + str(cam[1][3][0]) + ' ' + str(cam[1][3][1]) + ' ' + str(cam[1][3][2]) + ' ' + str(cam[1][3][3]) + '\n')

    f.close()

def load_pfm(file):
    color = None
    width = None
    height = None
    scale = None
    data_type = None
    header = str(file.readline()).rstrip()

    if header == 'PF':
        color = True
    elif header == 'Pf':
        color = False
    else:
        raise Exception('Not a PFM file.')
    dim_match = re.match(r'^(\d+)\s(\d+)\s$', file.readline())
    if dim_match:
        width, height = map(int, dim_match.groups())
    else:
        raise Exception('Malformed PFM header.')
    # scale = float(file.readline().rstrip())
    scale = float((file.readline()).rstrip())
    if scale < 0: # little-endian
        data_type = '<f'
    else:
        data_type = '>f' # big-endian
    data_string = file.read()
    data = np.fromstring(data_string, data_type)
    # data = np.fromfile(file, data_type)
    shape = (height, width, 3) if color else (height, width)
    data = np.reshape(data, shape)
    data = cv2.flip(data, 0)
    return data

def write_pfm(file, image, scale=1):
    file = file_io.FileIO(file, mode='wb')
    color = None

    if image.dtype.name != 'float32':
        raise Exception('Image dtype must be float32.')

    image = np.flipud(image)  

    if len(image.shape) == 3 and image.shape[2] == 3: # color image
        color = True
    elif len(image.shape) == 2 or len(image.shape) == 3 and image.shape[2] == 1: # greyscale
        color = False
    else:
        raise Exception('Image must have H x W x 3, H x W x 1 or H x W dimensions.')

    file.write('PF\n' if color else 'Pf\n')
    file.write('%d %d\n' % (image.shape[1], image.shape[0]))

    endian = image.dtype.byteorder

    if endian == '<' or endian == '=' and sys.byteorder == 'little':
        scale = -scale

    file.write('%f\n' % scale)

    image_string = image.tostring()
    file.write(image_string)    

    file.close()

def gen_dtu_resized_path(dtu_data_folder, mode='training'):
    """ generate data paths for dtu dataset """
    sample_list = []
    
    # parse camera pairs
    cluster_file_path = dtu_data_folder + '/Cameras/pair.txt'
    
    # cluster_list = open(cluster_file_path).read().split()
    cluster_list = file_io.FileIO(cluster_file_path, mode='r').read().split()

    # 3 sets
    training_set = [2, 6, 7, 8, 14, 16, 18, 19, 20, 22, 30, 31, 36, 39, 41, 42, 44,
                    45, 46, 47, 50, 51, 52, 53, 55, 57, 58, 60, 61, 63, 64, 65, 68, 69, 70, 71, 72,
                    74, 76, 83, 84, 85, 87, 88, 89, 90, 91, 92, 93, 94, 95, 96, 97, 98, 99, 100,
                    101, 102, 103, 104, 105, 107, 108, 109, 111, 112, 113, 115, 116, 119, 120,
                    121, 122, 123, 124, 125, 126, 127, 128]
    validation_set = [3, 5, 17, 21, 28, 35, 37, 38, 40, 43, 56, 59, 66, 67, 82, 86, 106, 117]

    data_set = []
    if mode == 'training':
        data_set = training_set
    elif mode == 'validation':
        data_set = validation_set

    # for each dataset
    for i in data_set:

        image_folder = os.path.join(dtu_data_folder, ('Rectified/scan%d_train' % i))
        cam_folder = os.path.join(dtu_data_folder, 'Cameras/train')
        depth_folder = os.path.join(dtu_data_folder, ('Depths/scan%d_train' % i))

        if mode == 'training':
            # for each lighting
            for j in range(0, 7):
                # for each reference image
                for p in range(0, int(cluster_list[0])):
                    paths = []
                    # ref image
                    ref_index = int(cluster_list[22 * p + 1])
                    ref_image_path = os.path.join(
                        image_folder, ('rect_%03d_%d_r5000.png' % ((ref_index + 1), j)))
                    ref_cam_path = os.path.join(cam_folder, ('%08d_cam.txt' % ref_index))
                    paths.append(ref_image_path)
                    paths.append(ref_cam_path)
                    # view images
                    for view in range(FLAGS.view_num - 1):
                        view_index = int(cluster_list[22 * p + 2 * view + 3])
                        view_image_path = os.path.join(
                            image_folder, ('rect_%03d_%d_r5000.png' % ((view_index + 1), j)))
                        view_cam_path = os.path.join(cam_folder, ('%08d_cam.txt' % view_index))
                        paths.append(view_image_path)
                        paths.append(view_cam_path)
                    # depth path
                    depth_image_path = os.path.join(depth_folder, ('depth_map_%04d.pfm' % ref_index))
                    paths.append(depth_image_path)
                    sample_list.append(paths)
        elif mode == 'validation':
            j = 3
            # for each reference image
            for p in range(0, int(cluster_list[0])):
                paths = []
                # ref image
                ref_index = int(cluster_list[22 * p + 1])
                ref_image_path = os.path.join(
                    image_folder, ('rect_%03d_%d_r5000.png' % ((ref_index + 1), j)))
                ref_cam_path = os.path.join(cam_folder, ('%08d_cam.txt' % ref_index))
                paths.append(ref_image_path)
                paths.append(ref_cam_path)
                # view images
                for view in range(FLAGS.view_num - 1):
                    view_index = int(cluster_list[22 * p + 2 * view + 3])
                    view_image_path = os.path.join(
                        image_folder, ('rect_%03d_%d_r5000.png' % ((view_index + 1), j)))
                    view_cam_path = os.path.join(cam_folder, ('%08d_cam.txt' % view_index))
                    paths.append(view_image_path)
                    paths.append(view_cam_path)
                # depth path
                depth_image_path = os.path.join(depth_folder, ('depth_map_%04d.pfm' % ref_index))
                paths.append(depth_image_path)
                sample_list.append(paths)
        
    return sample_list

def gen_dtu_mvs_path(dtu_data_folder, mode='training'):
    """ generate data paths for dtu dataset """
    sample_list = []
    
    # parse camera pairs
    cluster_file_path = dtu_data_folder + '/Cameras/pair.txt'
    cluster_list = open(cluster_file_path).read().split()

    # 3 sets
    training_set = [2, 6, 7, 8, 14, 16, 18, 19, 20, 22, 30, 31, 36, 39, 41, 42, 44,
                    45, 46, 47, 50, 51, 52, 53, 55, 57, 58, 60, 61, 63, 64, 65, 68, 69, 70, 71, 72,
                    74, 76, 83, 84, 85, 87, 88, 89, 90, 91, 92, 93, 94, 95, 96, 97, 98, 99, 100,
                    101, 102, 103, 104, 105, 107, 108, 109, 111, 112, 113, 115, 116, 119, 120,
                    121, 122, 123, 124, 125, 126, 127, 128]
    validation_set = [3, 5, 17, 21, 28, 35, 37, 38, 40, 43, 56, 59, 66, 67, 82, 86, 106, 117]
    evaluation_set = [1, 4, 9, 10, 11, 12, 13, 15, 23, 24, 29, 32, 33, 34, 48, 49, 62, 75, 77, 
                      110, 114, 118]

    # for each dataset
    data_set = []
    if mode == 'training':
        data_set = training_set
    elif mode == 'validation':
        data_set = validation_set
    elif mode == 'evaluation':
        data_set = evaluation_set

    # for each dataset
    for i in data_set:

        image_folder = os.path.join(dtu_data_folder, ('Rectified/scan%d' % i))
        cam_folder = os.path.join(dtu_data_folder, 'Cameras')
        depth_folder = os.path.join(dtu_data_folder, ('Depths/scan%d' % i))

        if mode == 'training':
            # for each lighting
            for j in range(0, 7):
                # for each reference image
                for p in range(0, int(cluster_list[0])):
                    paths = []
                    # ref image
                    ref_index = int(cluster_list[22 * p + 1])
                    ref_image_path = os.path.join(
                        image_folder, ('rect_%03d_%d_r5000.png' % ((ref_index + 1), j)))
                    ref_cam_path = os.path.join(cam_folder, ('%08d_cam.txt' % ref_index))
                    paths.append(ref_image_path)
                    paths.append(ref_cam_path)
                    # view images
                    for view in range(FLAGS.view_num - 1):
                        view_index = int(cluster_list[22 * p + 2 * view + 3])
                        view_image_path = os.path.join(
                            image_folder, ('rect_%03d_%d_r5000.png' % ((view_index + 1), j)))
                        view_cam_path = os.path.join(cam_folder, ('%08d_cam.txt' % view_index))
                        paths.append(view_image_path)
                        paths.append(view_cam_path)
                    # depth path
                    depth_image_path = os.path.join(depth_folder, ('depth_map_%04d.pfm' % ref_index))
                    paths.append(depth_image_path)
                    sample_list.append(paths)
        else:
            # for each reference image
            j = 5
            for p in range(0, int(cluster_list[0])):
                paths = []
                # ref image
                ref_index = int(cluster_list[22 * p + 1])
                ref_image_path = os.path.join(
                    image_folder, ('rect_%03d_%d_r5000.png' % ((ref_index + 1), j)))
                ref_cam_path = os.path.join(cam_folder, ('%08d_cam.txt' % ref_index))
                paths.append(ref_image_path)
                paths.append(ref_cam_path)
                # view images
                for view in range(FLAGS.view_num - 1):
                    view_index = int(cluster_list[22 * p + 2 * view + 3])
                    view_image_path = os.path.join(
                        image_folder, ('rect_%03d_%d_r5000.png' % ((view_index + 1), j)))
                    view_cam_path = os.path.join(cam_folder, ('%08d_cam.txt' % view_index))
                    paths.append(view_image_path)
                    paths.append(view_cam_path)
                # depth path
                depth_image_path = os.path.join(depth_folder, ('depth_map_%04d.pfm' % ref_index))
                paths.append(depth_image_path)
                sample_list.append(paths)
        
    return sample_list



def gen_data(data_root, val_ratio = 0.1):
    """ The data_root directory should contain multiple sessions, which are then parsed into
    the data structure that the train data generator expects """
    session_list = os.listdir(data_root)
    




def gen_mvs_list(mode='training'):
    """output paths in a list: [[I1_path1,  C1_path, I2_path, C2_path, ...(, D1_path)], [...], ...]"""
    sample_list = []
    if FLAGS.train_dtu:
        dtu_sample_list = gen_dtu_mvs_path(FLAGS.dtu_data_root, mode=mode)
        sample_list = sample_list + dtu_sample_list
    return sample_list

# for testing
def gen_pipeline_mvs_list(dense_folder):
    """ mvs input path list """
    image_folder = os.path.join(dense_folder, 'images')
    cam_folder = os.path.join(dense_folder, 'cams')
    cluster_list_path = os.path.join(dense_folder, 'pair.txt')
    cluster_list = open(cluster_list_path).read().split()

    # for each dataset
    mvs_list = []
    pos = 1
    for i in range(int(cluster_list[0])):
        paths = []
        # ref image
        ref_index = int(cluster_list[pos])
        pos += 1
        ref_image_path = os.path.join(image_folder, ('%08d.jpg' % ref_index))
        ref_cam_path = os.path.join(cam_folder, ('%08d_cam.txt' % ref_index))
        paths.append(ref_image_path)
        paths.append(ref_cam_path)
        # view images
        all_view_num = int(cluster_list[pos])
        pos += 1
        check_view_num = min(FLAGS.view_num - 1, all_view_num)
        for view in range(check_view_num):
            view_index = int(cluster_list[pos + 2 * view])
            view_image_path = os.path.join(image_folder, ('%08d.jpg' % view_index))
            view_cam_path = os.path.join(cam_folder, ('%08d_cam.txt' % view_index))
            paths.append(view_image_path)
            paths.append(view_cam_path)
        pos += 2 * all_view_num
        # depth path
        mvs_list.append(paths)
    return mvs_list
