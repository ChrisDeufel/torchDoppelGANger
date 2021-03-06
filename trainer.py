import sys
import numpy as np
import torch
from torch.utils.tensorboard import SummaryWriter
import os
import logging
from loss_util import gradient_penalty
from gan.network import AttrDiscriminator, Discriminator, DoppelGANgerGeneratorRNN, DoppelGANgerGeneratorAttention, \
    TransformerDiscriminator
from util import calculate_mmd_rbf

def add_handler(logger, handlers):
    for handler in handlers:
        logger.addHandler(handler)

def setup_logging(time_logging_file, config_logging_file):
    # SET UP LOGGING
    config_logger = logging.getLogger("config_logger")
    config_logger.setLevel(logging.INFO)
    # config_logger.setLevel(logging.INFO)
    time_logger = logging.getLogger("time_logger")
    time_logger.setLevel(logging.INFO)
    # time_logger.setLevel(logging.INFO)
    # set up time handler
    time_formatter = logging.Formatter('%(asctime)s:%(message)s')
    time_handler = logging.FileHandler(time_logging_file)
    time_handler.setLevel(logging.INFO)
    time_handler.setFormatter(time_formatter)
    stream_handler = logging.StreamHandler()
    stream_handler.setLevel(logging.INFO)
    stream_handler.setFormatter(time_formatter)
    add_handler(time_logger, [time_handler, stream_handler])
    # setup config handler
    config_formatter = logging.Formatter('%(message)s')
    config_handler = logging.FileHandler(config_logging_file)
    config_handler.setLevel(logging.INFO)
    config_handler.setFormatter(config_formatter)
    config_logger.addHandler(config_handler)
    return config_logger, time_logger


class Trainer:
    def __init__(self,
                 real_train_dl,
                 device,
                 checkpoint_dir='',
                 time_logging_file='',
                 config_logging_file='',
                 noise_dim=5,
                 sample_len=10,
                 batch_size=100,
                 dis_lambda_gp=10,
                 attr_dis_lambda_gp=10,
                 g_attr_d_coe=1,
                 d_rounds=1,
                 g_rounds=1,
                 gen_type='RNN',
                 dis_type='normal',
                 att_dim=100,
                 num_heads=10,
                 g_lr=0.0001,
                 g_beta1=0.5,
                 d_lr=0.0001,
                 d_beta1=0.5,
                 attr_d_lr=0.0001,
                 attr_d_beta1=0.5
                 ):
        self.config_logger, self.time_logger = setup_logging(time_logging_file, config_logging_file)
        # setup models
        if dis_type == 'TRANSFORMER':
            self.dis = TransformerDiscriminator(input_feature_shape=real_train_dl.dataset.data_feature_shape,
                                                input_attribute_shape=real_train_dl.dataset.data_attribute_shape)
        else:
            self.dis = Discriminator(real_train_dl.dataset.data_feature_shape,
                                     real_train_dl.dataset.data_attribute_shape)
        self.config_logger.info("DISCRIMINATOR: {0}".format(self.dis))
        self.attr_dis = AttrDiscriminator(real_train_dl.dataset.data_attribute_shape)
        self.config_logger.info("ATTRIBUTE DISCRIMINATOR: {0}".format(self.attr_dis))
        if gen_type == 'RNN':
            noise_dim = noise_dim
        else:
            noise_dim = att_dim - real_train_dl.dataset.data_attribute.shape[1]
        if gen_type == "RNN":
            self.gen = DoppelGANgerGeneratorRNN(noise_dim=noise_dim,
                                                feature_outputs=real_train_dl.dataset.data_feature_outputs,
                                                attribute_outputs=real_train_dl.dataset.data_attribute_outputs,
                                                real_attribute_mask=real_train_dl.dataset.real_attribute_mask,
                                                device=device,
                                                sample_len=sample_len)
        else:
            self.gen = DoppelGANgerGeneratorAttention(noise_dim=noise_dim,
                                                      feature_outputs=real_train_dl.dataset.data_feature_outputs,
                                                      attribute_outputs=real_train_dl.dataset.data_attribute_outputs,
                                                      real_attribute_mask=real_train_dl.dataset.real_attribute_mask,
                                                      device=device,
                                                      sample_len=sample_len, num_heads=num_heads, attn_dim=att_dim)
        self.config_logger.info("GENERATOR: {0}".format(self.gen))
        self.criterion = "Wasserstein GAN with Gradient Penalty"
        self.config_logger.info("Criterion: {0}".format(self.criterion))
        # setup optimizer
        self.dis_opt = torch.optim.Adam(self.dis.parameters(), lr=d_lr, betas=(d_beta1, 0.999))
        self.config_logger.info("DISCRIMINATOR OPTIMIZER: {0}".format(self.dis_opt))
        self.attr_dis_opt = torch.optim.Adam(self.attr_dis.parameters(), lr=attr_d_lr, betas=(attr_d_beta1, 0.999))
        self.config_logger.info("ATTRIBUTE DISCRIMINATOR OPTIMIZER: {0}".format(self.attr_dis_opt))
        self.gen_opt = torch.optim.Adam(self.gen.parameters(), lr=g_lr, betas=(g_beta1, 0.999))
        self.config_logger.info("GENERATOR OPTIMIZER: {0}".format(self.gen_opt))
        self.real_train_dl = real_train_dl
        self.data_feature_shape = self.real_train_dl.dataset.data_feature_shape
        if self.data_feature_shape[1] % sample_len != 0:
            raise Exception("length must be a multiple of sample_len")
        self.sample_time = int(self.data_feature_shape[1] / sample_len)
        self.batch_size = batch_size
        self.config_logger.info("Batch Size: {0}".format(self.batch_size))
        self.noise_dim = noise_dim
        self.config_logger.info("Noise Dimension: {0}".format(self.noise_dim))
        self.sample_len = sample_len
        self.config_logger.info("Sample_Length: {0}".format(self.sample_len))
        self.dis_lambda_gp = dis_lambda_gp
        self.attr_dis_lambda_gp = attr_dis_lambda_gp
        self.g_attr_d_coe = g_attr_d_coe
        self.d_rounds = d_rounds
        self.g_rounds = g_rounds
        self.config_logger.info("d_rounds: {0}".format(self.d_rounds))
        self.config_logger.info("g_rounds: {0}".format(self.g_rounds))
        self.config_logger.info("d_lambda_gp_coefficient: {0}".format(self.dis_lambda_gp))
        self.config_logger.info("attr_d_lambda_gp_coefficient: {0}".format(self.attr_dis_lambda_gp))
        self.config_logger.info("g_attr_d_coe: {0}".format(self.g_attr_d_coe))

        self.checkpoint_dir = checkpoint_dir
        if not os.path.exists(checkpoint_dir):
            os.makedirs(checkpoint_dir)
        self.writer = SummaryWriter(checkpoint_dir)
        self.device = device
        self.config_logger.info("Device: {0}".format(self.device))
        self.dis = self.dis.to(self.device)
        self.attr_dis = self.attr_dis.to(self.device)
        self.gen = self.gen.to(self.device)
        self.EPS = 1e-8

    # TODO: use helper function in gan_util
    def gen_attribute_input_noise(self, num_sample):
        return torch.randn(size=[num_sample, self.noise_dim])

    def gen_feature_input_noise(self, num_sample, length):
        return torch.randn(size=[num_sample, length, self.noise_dim])

    def save(self, epoch):
        if not os.path.exists("{0}/epoch_{1}".format(self.checkpoint_dir, epoch)):
            os.makedirs("{0}/epoch_{1}".format(self.checkpoint_dir, epoch))
        torch.save(self.dis, "{0}/epoch_{1}/discriminator.pth".format(self.checkpoint_dir, epoch))
        torch.save(self.attr_dis, "{0}/epoch_{1}/attr_discriminator.pth".format(self.checkpoint_dir, epoch))
        torch.save(self.gen, "{0}/epoch_{1}/generator.pth".format(self.checkpoint_dir, epoch))

    def inference(self, epoch, model_dir=None):
        if model_dir is None:
            model_dir = "{0}/epoch_{1}".format(self.checkpoint_dir, epoch)
        batch_size = self.batch_size

        while self.real_train_dl.dataset.data_attribute_shape[0] % batch_size != 0:
            batch_size -= 1
        rounds = self.real_train_dl.dataset.data_attribute_shape[0] // batch_size
        sampled_features = np.zeros((0, self.real_train_dl.dataset.data_feature_shape[1],
                                     self.real_train_dl.dataset.data_feature_shape[2]-2))
        sampled_attributes = np.zeros((0, self.real_train_dl.dataset.data_attribute_shape[1]))
        sampled_gen_flags = np.zeros((0, self.real_train_dl.dataset.data_feature_shape[1]))
        sampled_lengths = np.zeros(0)
        for i in range(rounds):
            features, attributes, gen_flags, lengths = self.sample_from(batch_size=batch_size)
            sampled_features = np.concatenate((sampled_features, features), axis=0)
            sampled_attributes = np.concatenate((sampled_attributes, attributes), axis=0)
            sampled_gen_flags = np.concatenate((sampled_gen_flags, gen_flags), axis=0)
            sampled_lengths = np.concatenate((sampled_lengths, lengths), axis=0)
        np.savez("{0}/generated_samples.npz".format(model_dir), sampled_features=sampled_features,
                 sampled_attributes=sampled_attributes, sampled_gen_flags=sampled_gen_flags,
                 sampled_lengths=sampled_lengths)

    def load(self, model_dir=None):
        if not os.path.exists(model_dir):
            raise Exception("Directory to load pytorch model doesn't exist")
        self.dis = torch.load("{0}/discriminator.pth".format(model_dir))
        self.attr_dis = torch.load("{0}/attr_discriminator.pth".format(model_dir))
        self.gen = torch.load("{0}/generator.pth".format(model_dir))
        self.dis = self.dis.to(self.device)
        self.attr_dis = self.attr_dis.to(self.device)
        self.gen = self.gen.to(self.device)
        self.dis.device = self.device
        self.attr_dis.device = self.device
        self.gen.device = self.device

    def sample_from(self, batch_size, return_gen_flag_feature=False):
        real_attribute_noise = self.gen_attribute_input_noise(batch_size).to(self.device)
        addi_attribute_noise = self.gen_attribute_input_noise(batch_size).to(self.device)
        feature_input_noise = self.gen_feature_input_noise(batch_size, self.sample_time).to(self.device)
        self.dis.eval()
        self.attr_dis.eval()
        self.gen.eval()
        with torch.no_grad():
            attributes, features = self.gen(real_attribute_noise,
                                            addi_attribute_noise,
                                            feature_input_noise)
            attributes = attributes.cpu().numpy()
            features = features.cpu().numpy()

            # TODO: possible without loop?!
            gen_flags = np.zeros(features.shape[:-1])
            lengths = np.zeros(features.shape[0])
            for i in range(len(features)):
                winner = (features[i, :, -1] > features[i, :, -2])
                argmax = np.argmax(winner == True)
                if argmax == 0:
                    gen_flags[i, :] = 1
                else:
                    gen_flags[i, :argmax] = 1
                lengths[i] = argmax
            if not return_gen_flag_feature:
                features = features[:, :, :-2]
        return features, attributes, gen_flags, lengths

    def calculate_gp_dis(self, batch_size, fake_feature, data_feature, fake_attribute, data_attribute):
        alpha_dim2 = torch.FloatTensor(batch_size, 1).uniform_(1).to(self.device)
        alpha_dim3 = torch.unsqueeze(alpha_dim2, 2).to(self.device)
        differences_input_feature = (fake_feature -
                                     data_feature)
        interpolates_input_feature = (data_feature +
                                      alpha_dim3 * differences_input_feature)
        differences_input_attribute = (fake_attribute -
                                       data_attribute)
        interpolates_input_attribute = (data_attribute +
                                        (alpha_dim2 *
                                         differences_input_attribute))
        mixed_scores = self.dis(interpolates_input_feature,
                                interpolates_input_attribute)
        gradients = torch.autograd.grad(
            inputs=[interpolates_input_feature, interpolates_input_attribute],
            outputs=mixed_scores,
            grad_outputs=torch.ones_like(mixed_scores),
            create_graph=True,
            retain_graph=True
        )
        slopes1 = torch.sum(torch.square(gradients[0]),
                            dim=(1, 2))
        slopes2 = torch.sum(torch.square(gradients[1]),
                            dim=(1))
        slopes = torch.sqrt(slopes1 + slopes2 + self.EPS)
        loss_dis_gp = torch.mean((slopes - 1.) ** 2)
        loss_dis_gp_unflattened = (slopes - 1.) ** 2
        return loss_dis_gp, loss_dis_gp_unflattened

    def calculate_gp_attr_dis(self, batch_size, fake_attribute, data_attribute):
        alpha_dim2 = torch.FloatTensor(batch_size, 1).uniform_(1).to(self.device)
        differences_input_attribute = (fake_attribute -
                                       data_attribute)
        interpolates_input_attribute = (data_attribute +
                                        (alpha_dim2 *
                                         differences_input_attribute))
        mixed_scores = self.attr_dis(interpolates_input_attribute)
        gradients = torch.autograd.grad(
            inputs=interpolates_input_attribute,
            outputs=mixed_scores,
            grad_outputs=torch.ones_like(mixed_scores),
            create_graph=True,
            retain_graph=True
        )
        slopes1 = torch.sum(torch.square(gradients[0]),
                            dim=(1))
        slopes = torch.sqrt(slopes1 + self.EPS)
        loss_attr_dis_gp = torch.mean((slopes - 1.) ** 2)
        loss_attr_dis_gp_unflattened = (slopes - 1.) ** 2
        return loss_attr_dis_gp, loss_attr_dis_gp_unflattened

    def add_losses(self, running_losses, writer_frequency, epoch, n_total_steps, batch_idx):
        self.writer.add_scalar('loss/d_wo_gp', running_losses["dis_wogp_rl"] / writer_frequency,
                               epoch * n_total_steps + batch_idx)
        self.writer.add_scalar('loss/d_total', running_losses["dis_total_rl"] / writer_frequency,
                               epoch * n_total_steps + batch_idx)
        self.writer.add_scalar('loss/d/fake', running_losses["dis_fake_rl"] / writer_frequency,
                               epoch * n_total_steps + batch_idx)
        self.writer.add_scalar('loss/d/real', running_losses["dis_real_rl"] / writer_frequency,
                               epoch * n_total_steps + batch_idx)
        self.writer.add_scalar('loss/d/gp', running_losses["dis_gp_rl"] / writer_frequency,
                               epoch * n_total_steps + batch_idx)
        self.writer.add_scalar('loss/attr_d', running_losses["attr_dis_rl"] / writer_frequency,
                               epoch * n_total_steps + batch_idx)
        self.writer.add_scalar('loss/attr_d/fake', running_losses["attr_dis_fake_rl"] / writer_frequency,
                               epoch * n_total_steps + batch_idx)
        self.writer.add_scalar('loss/attr_d/real', running_losses["attr_dis_real_rl"] / writer_frequency,
                               epoch * n_total_steps + batch_idx)
        self.writer.add_scalar('loss/attr_d/gp', running_losses["attr_dis_gp_rl"] / writer_frequency,
                               epoch * n_total_steps + batch_idx)
        self.writer.add_scalar('loss/g/d', running_losses["gen_d_rl"] / writer_frequency,
                               epoch * n_total_steps + batch_idx)
        self.writer.add_scalar('loss/g/attr_d', running_losses["gen_attr_d_rl"] / writer_frequency,
                               epoch * n_total_steps + batch_idx)
        self.writer.add_scalar('loss/g', running_losses["gen_rl"] / writer_frequency,
                               epoch * n_total_steps + batch_idx)
        running_losses["dis_total_rl"] = 0
        running_losses["dis_wogp_rl"] = 0
        running_losses["dis_fake_rl"] = 0
        running_losses["dis_real_rl"] = 0
        running_losses["dis_gp_rl"] = 0
        running_losses["attr_dis_rl"] = 0
        running_losses["attr_dis_fake_rl"] = 0
        running_losses["attr_dis_real_rl"] = 0
        running_losses["attr_dis_gp_rl"] = 0
        running_losses["gen_d_rl"] = 0
        running_losses["gen_rl"] = 0
        running_losses["gen_attr_d_rl"] = 0
        return running_losses

    def train(self, epochs, writer_frequency=1, saver_frequency=10):
        self.dis.train()
        self.attr_dis.train()
        self.gen.train()

        # add models to writer
        # self.writer.add_graph(model=self.dis, input_to_model=[
        #     torch.randn([1, self.dis.input_feature_shape[1], self.dis.input_feature_shape[2]]).to(self.device),
        #     torch.randn([1, self.dis.input_attribute_shape[1]]).to(self.device)])
        # self.writer.add_graph(self.attr_dis, input_to_model=torch.randn([1, self.attr_dis.input_size]).to(self.device))
        # self.writer.add_graph(self.gen,
        #                       input_to_model=[torch.randn(1, self.noise_dim).to(self.device),
        #                                       torch.randn(1, self.noise_dim).to(self.device),
        #                                       torch.randn(1, self.sample_time, self.noise_dim).to(self.device)])

        # create all running losses (rl) dict
        running_losses = {
            "dis_total_rl": 0,
            "dis_wogp_rl": 0,
            "dis_fake_rl": 0,
            "dis_real_rl": 0,
            "dis_gp_rl": 0,
            "attr_dis_rl": 0,
            "attr_dis_fake_rl": 0,
            "attr_dis_real_rl": 0,
            "attr_dis_gp_rl": 0,
            "gen_rl": 0,
            "gen_d_rl": 0,
            "gen_attr_d_rl": 0
        }
        avg_mmd = []
        n_total_steps = len(self.real_train_dl)
        for epoch in range(epochs):
            self.dis.train()
            self.attr_dis.train()
            self.gen.train()
            mmd = []
            for batch_idx, (data_attribute, data_feature) in enumerate(self.real_train_dl):
                data_attribute = data_attribute.to(self.device)
                data_feature = data_feature.to(self.device)
                batch_size = data_attribute.shape[0]
                # Train Critic: max E[critic(real)] - E[critic(fake)]
                for _ in range(self.d_rounds):
                    real_attribute_noise = self.gen_attribute_input_noise(batch_size).to(self.device)
                    addi_attribute_noise = self.gen_attribute_input_noise(batch_size).to(self.device)
                    feature_input_noise = self.gen_feature_input_noise(batch_size, self.sample_time).to(self.device)
                    fake_attribute, fake_feature = self.gen(real_attribute_noise,
                                                            addi_attribute_noise,
                                                            feature_input_noise)
                    mmd.append(calculate_mmd_rbf(torch.mean(fake_feature, dim=0).detach().cpu().numpy(),
                                                 torch.mean(data_feature, dim=0).detach().cpu().numpy()))
                    # discriminator
                    dis_real = self.dis(data_feature, data_attribute)
                    dis_fake = self.dis(fake_feature, fake_attribute)

                    loss_dis_fake = torch.mean(dis_fake)
                    loss_dis_real = -torch.mean(dis_real)
                    running_losses["dis_wogp_rl"] += (loss_dis_fake + loss_dis_real).item()

                    # calculate gradient penalty
                    # TODO:    ALL THIS UNFLATTEN STUFF IS ONLY FOR SPECIAL LOSSES (SEE DOPPELGANGER BUILD LOSS)
                    dis_fake_unflattened = dis_fake
                    dis_real_unflattened = -dis_real
                    loss_dis_gp, loss_dis_gp_unflattened = self.calculate_gp_dis(batch_size, fake_feature, data_feature,
                                                                                 fake_attribute, data_attribute)
                    loss_dis = loss_dis_fake + loss_dis_real + self.dis_lambda_gp * loss_dis_gp
                    d_loss_unflattened = (dis_fake_unflattened +
                                          dis_real_unflattened +
                                          self.dis_lambda_gp * loss_dis_gp_unflattened)
                    self.dis.zero_grad()
                    loss_dis.backward(retain_graph=True)
                    self.dis_opt.step()

                    running_losses["dis_total_rl"] += loss_dis.item()
                    running_losses["dis_fake_rl"] += loss_dis_fake.item()
                    running_losses["dis_real_rl"] += loss_dis_real.item()
                    running_losses["dis_gp_rl"] += loss_dis_gp.item()

                    # attribute discriminator
                    attr_dis_real = self.attr_dis(data_attribute)
                    attr_dis_fake = self.attr_dis(fake_attribute)
                    loss_attr_dis_real = -torch.mean(attr_dis_real)
                    loss_attr_dis_fake = torch.mean(attr_dis_fake)
                    # calculate gradient penalty
                    attr_dis_real_unflattened = -attr_dis_real
                    attr_dis_fake_unflattened = attr_dis_fake
                    loss_attr_dis_gp, loss_attr_dis_gp_unflattened = self.calculate_gp_attr_dis(batch_size,
                                                                                                fake_attribute,
                                                                                                data_attribute)
                    loss_attr_dis = loss_attr_dis_fake + loss_attr_dis_real + self.attr_dis_lambda_gp * loss_attr_dis_gp
                    loss_attr_dis_unflattened = (attr_dis_fake_unflattened +
                                                 attr_dis_real_unflattened +
                                                 self.attr_dis_lambda_gp * loss_attr_dis_gp_unflattened)

                    self.attr_dis.zero_grad()
                    loss_attr_dis.backward(retain_graph=True)
                    self.attr_dis_opt.step()

                    running_losses["attr_dis_rl"] += loss_attr_dis.item()
                    running_losses["attr_dis_fake_rl"] += loss_attr_dis_fake.item()
                    running_losses["attr_dis_real_rl"] += loss_attr_dis_real.item()
                    running_losses["attr_dis_gp_rl"] += loss_attr_dis_gp.item()

                # Train Generator: max E[critic(gen_fake)] <-> min -E[critic(gen_fake)]
                for _ in range(self.g_rounds):
                    gen_d_fake = self.dis(fake_feature, fake_attribute)
                    gen_attr_d_fake = self.attr_dis(fake_attribute)
                    loss_gen_d = -torch.mean(gen_d_fake)
                    loss_gen_attr_d = -torch.mean(gen_attr_d_fake)
                    loss_gen = loss_gen_d + self.g_attr_d_coe * loss_gen_attr_d
                    self.gen.zero_grad()
                    loss_gen.backward()
                    self.gen_opt.step()

                    running_losses["gen_d_rl"] += loss_gen_d.item()
                    running_losses["gen_attr_d_rl"] += loss_gen_attr_d.item()
                    running_losses["gen_rl"] += loss_gen.item()
                    # write losses to summary writer
                    if (batch_idx + 1) % writer_frequency == 0:
                        running_losses = self.add_losses(running_losses, writer_frequency, epoch, n_total_steps,
                                                         batch_idx)


            self.time_logger.info('END OF EPOCH {0}'.format(epoch))
            avg_mmd.append(np.asarray(mmd).mean())
            # save model
            if epoch % saver_frequency == 0:
                self.save(epoch)
                self.inference(epoch)
        np.save("{}/mmd.npy".format(self.checkpoint_dir), np.asarray(avg_mmd))
        self.writer.close()
