from torch.utils.data import DataLoader
import torch
import numpy as np
from trainer import Trainer
from gan.network import Discriminator, AttrDiscriminator, DoppelGANgerGenerator
from load_data import load_data
from util import normalize_per_sample, add_gen_flag

sample_len = 10
batch_size = 100
noise_dim = 5
# load data
dataset = 'web'

(data_feature, data_attribute,
 data_gen_flag,
 data_feature_outputs, data_attribute_outputs) = load_data("data/{0}".format(dataset))

(data_feature, data_attribute,
 data_attribute_outputs, real_attribute_mask) = normalize_per_sample(data_feature, data_attribute,
                                                                     data_feature_outputs, data_attribute_outputs)

data_feature, data_feature_outputs = add_gen_flag(data_feature, data_gen_flag, data_feature_outputs, sample_len)

# generate discriminators and generator
discriminator = Discriminator(data_feature, data_attribute)
attr_discriminator = AttrDiscriminator(data_attribute)
generator = DoppelGANgerGenerator(noise_dim=noise_dim, feature_outputs=data_feature_outputs,
                                  attribute_outputs=data_attribute_outputs,
                                  real_attribute_mask=real_attribute_mask, sample_len=sample_len)
# define optimizer
g_lr = 0.001
g_beta1 = 0.5
d_lr = 0.001
d_beta1 = 0.5
attr_d_lr = 0.001
attr_d_beta1 = 0.5
attr_opt = torch.optim.Adam(discriminator.parameters(), lr=d_lr, betas=(d_beta1, d_beta1))
d_attr_opt = torch.optim.Adam(attr_discriminator.parameters(), lr=attr_d_lr, betas=(attr_d_beta1, attr_d_beta1))
gen_opt = torch.optim.Adam(generator.parameters(), lr=g_lr, betas=(g_beta1, g_beta1))
data_feature_shape = data_feature.shape
# define Hyperparameters
epoch = 400
vis_freq = 500
vis_num_sample = 5
d_rounds = 1
g_rounds = 1
d_gp_coe = 10.0
attr_d_gp_coe = 10.0
g_attr_d_coe = 1.0
extra_checkpoint_freq = 5
num_packing = 1

model_dir = "runs/web_12/checkpoint/epoch_70"
trainer = Trainer(discriminator=discriminator, attr_discriminator=attr_discriminator, generator=generator,
                  criterion=None, dis_optimizer=attr_opt, addi_dis_optimizer=d_attr_opt, gen_optimizer=gen_opt,
                  real_train_dl=None, data_feature_shape=data_feature_shape)
trainer.load(model_dir)


# start sampling
real_attribute_input_noise = trainer.gen_attribute_input_noise(batch_size)
addi_attribute_input_noise = trainer.gen_attribute_input_noise(batch_size)
feature_input_noise = trainer.gen_feature_input_noise(batch_size, trainer.sample_time)
# for the start we want to 'produce' as many samples as we have data available
rounds = data_attribute.shape[0] // batch_size
sampled_features = np.zeros((0, data_feature.shape[1], data_feature.shape[2] - 2))
sampled_attributes = np.zeros((0, data_attribute.shape[1]))
sampled_gen_flags = np.zeros((0, data_feature.shape[1]))
sampled_lengths = np.zeros(0)
for i in range(rounds):
    real_attribute_input_noise = trainer.gen_attribute_input_noise(batch_size)
    addi_attribute_input_noise = trainer.gen_attribute_input_noise(batch_size)
    feature_input_noise = trainer.gen_feature_input_noise(batch_size, trainer.sample_time)
    features, attributes, gen_flags, lengths = trainer.sample_from(real_attribute_input_noise,
                                                                   addi_attribute_input_noise,
                                                                   feature_input_noise)
    sampled_features = np.concatenate((sampled_features, features), axis=0)
    sampled_attributes = np.concatenate((sampled_attributes, attributes), axis=0)
    sampled_gen_flags = np.concatenate((sampled_gen_flags, gen_flags), axis=0)
    sampled_lengths = np.concatenate((sampled_lengths, lengths), axis=0)
np.savez("{0}/generated_samples.npz".format(model_dir), sampled_features=sampled_features,
         sampled_attributes=sampled_attributes, sampled_gen_flags=sampled_gen_flags,
         sampled_lengths=sampled_lengths)