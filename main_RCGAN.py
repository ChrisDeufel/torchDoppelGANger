from torch.utils.data import DataLoader
import torch
import logging
from data import Data
import os
from trainer_RCGAN import RCGAN, add_handler_trainer, add_config_handler_trainer

dataset_name = "index_growth_1mo"
batch_size = 20
dataset = Data(name=dataset_name, sample_len=1)
#device = "cuda" if torch.cuda.is_available() else "cpu"
device = "cpu"
gan_type = "RCGAN"
if gan_type == 'RGAN':
    isConditional = False
else:
    isConditional = True
checkpoint_dir = 'runs/{}/{}/test/checkpoint'.format(dataset_name, gan_type)
if not os.path.exists(checkpoint_dir):
    os.makedirs(checkpoint_dir)

time_logging_file = 'runs/{}/{}/test/time.log'.format(dataset_name, gan_type)
config_logging_file = 'runs/{}/{}/test/config.log'.format(dataset_name, gan_type)
# SET UP LOGGING
# logger = logging.getLogger(__name__)
# logger.setLevel(logging.DEBUG)
# set up time handler
time_formatter = logging.Formatter('%(asctime)s:%(message)s')
time_handler = logging.FileHandler(time_logging_file)
time_handler.setLevel(logging.INFO)
time_handler.setFormatter(time_formatter)
stream_handler = logging.StreamHandler()
stream_handler.setLevel(logging.INFO)
stream_handler.setFormatter(time_formatter)
add_handler_trainer([time_handler, stream_handler])
# setup config handler
config_formatter = logging.Formatter('%(message)s')
config_handler = logging.FileHandler(config_logging_file)
config_handler.setLevel(logging.INFO)
config_handler.setFormatter(config_formatter)
add_config_handler_trainer([config_handler])


real_train_dl = DataLoader(dataset=dataset, batch_size=batch_size, shuffle=True)
trainer = RCGAN(real_train_dl, device=device, checkpoint_dir=checkpoint_dir, isConditional=isConditional)
epochs = 400
trainer.train(epochs=epochs, saver_frequency=20)
