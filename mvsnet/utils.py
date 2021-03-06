import logging
import tensorflow as tf
import os
import subprocess

"""
A small library of helper functions sued through the mvsnet package 
"""


def set_log_level(logger):
    """ Grabs log level from command line """
    try:
        level = os.environ['LOG_LEVEL'].upper()
        exec('logger.setLevel(logging.{})'.format(level))
    except Exception as e:
        logger.setLevel(logging.INFO)


def setup_logger(name):
    """ Sets up a logger, grabbing log_level from command line """
    logging.basicConfig()
    logger = logging.getLogger(name)
    try:
        set_log_level(logger)
    except Exception as e:
        logger.setLevel(logging.INFO)
        logger.warn('Failed to set log level with exception {}'.format(e))
    return logger


def init_session():
    """ Returns tf global vars initializer and sets the config """
    init_op = tf.global_variables_initializer()
    config = tf.ConfigProto(allow_soft_placement=True)
    config.gpu_options.allow_growth = True
    config.inter_op_parallelism_threads = 0
    config.intra_op_parallelism_threads = 0
    return init_op, config


def mkdir_p(dir_path):
    """ Makes the directory dir_path if it doesn't exist """
    if not os.path.isdir(dir_path):
        os.mkdir(dir_path)



def makedirs_p(dir_path):
    """ Makes the directories up to dir_path if they doesn't exist """
    if not os.path.isdir(dir_path):
        os.makedirs(dir_path)

def ml_engine():
    if 'CLOUD_ML_JOB_ID' in os.environ:
        return True
    else:
        return False


def initialize_wandb(args, project='mvsnet'):
    import wandb
    wandb_key = "08b2fe7c6c5d56f49b9c2dee8f24ca14c0679509"
    if ml_engine():
        subprocess.call(["/root/.local/bin/wandb", "login", wandb_key])
    else:
        try:
            subprocess.call(["wandb", "login", wandb_key])
        except Exception as e:
            subprocess.call([ "python", "-m", "wandb.cli", "login", wandb_key])
    wandb.init(project=project, name=args.run_name)
    wandb.config.update(args, allow_val_change=True)


def ckpt_path(base_dir, regularization, network_mode, build = False):
    """ Constructs the base file path for model check points. We use a convention models with different attributes are saved / loaded
    in different subdirs of a base_dir. This is base path to the colleciton of checkpoints. For a specific checkpoint, you also need to call
    model_path(...)
    Args:
        base_dir: base model directory
        regularization: see FLAGS.regularization
        network_mode: see FLAGS.network_mode
        build: A boolean indicating whether we should try to create this path
    """
    full_model_dir = os.path.join(base_dir, regularization, network_mode)
    if build and not ml_engine():
        makedirs_p(full_model_dir)
    ckpt_path = os.path.join(full_model_dir, 'model.ckpt')
    return ckpt_path


def model_path(ckpt_path, ckpt_step):
    """ Constructs the path to a specific saved model at a specific ckpt_stpe """
    model_path = '-'.join([ckpt_path, str(ckpt_step)])
    return model_path





