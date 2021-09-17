import math
import torch
import socket
import argparse
import os
import numpy as np
from sklearn.manifold import TSNE
import scipy.misc
import matplotlib

matplotlib.use('agg')
import matplotlib.pyplot as plt
import functools
from skimage.metrics import peak_signal_noise_ratio as psnr_metric
from skimage.metrics import structural_similarity as ssim_metric
from scipy import signal
from scipy import ndimage
from PIL import Image, ImageDraw

from torchvision import datasets, transforms
from torch.autograd import Variable
import imageio

hostname = socket.gethostname()

RGB_weights = torch.tensor(np.array([0.299, 0.587, 0.114]), dtype=torch.float32, device=torch.device('cuda:0'),
                           requires_grad=False).detach()


def torch_rgb_img_to_gray(tensor):
    # in: Bx3xHxW  out: Bx1xHxW
    if tensor.shape[1] == 1:
        return tensor
    # assert tensor.shape[1] == 3  # make sure input image has 3 (RGB) channels
    tensor = torch.transpose(tensor, 1, 3)  # B x W x H x 3
    tensor = torch.unsqueeze(torch.matmul(tensor, RGB_weights), -1)  # B x W x H x 1
    tensor = torch.transpose(tensor, 3, 1)  # B x 1 x H x W
    return tensor


def torch_tensor_to_img(tensor):
    image_array = tensor.numpy()
    image_array -= np.min(image_array)
    image_array = np.minimum(image_array, 1.0)
    # print(image_array.shape)
    image_array = np.transpose(image_array, (1, 2, 0))
    img = None
    if image_array.shape[2] == 3:  # 3-channel image
        # array is grayscale, but we convert to RGB
        img = Image.fromarray((image_array * 255).astype('uint8'), mode='RGB')
    else:
        img = Image.fromarray((image_array * 255).astype('uint8'), mode='L').convert('RGB')
    return img


def load_dataset(opt, sequential=None, implausible=None):
    train_data = None
    test_data = None
    if opt.dataset == 'smmnist':
        from data.moving_mnist import MovingMNIST
        train_data = MovingMNIST(
            train=True,
            data_root=opt.data_root,
            seq_len=opt.n_past + opt.n_future,
            image_size=opt.image_width,
            deterministic=False,
            num_digits=opt.num_digits)
        test_data = MovingMNIST(
            train=False,
            data_root=opt.data_root,
            seq_len=opt.n_eval,
            image_size=opt.image_width,
            deterministic=False,
            num_digits=opt.num_digits)
    elif opt.dataset == 'bair':
        from data.bair import RobotPush
        train_data = RobotPush(
            data_root=opt.data_root,
            train=True,
            seq_len=opt.n_past + opt.n_future,
            image_size=opt.image_width)
        test_data = RobotPush(
            data_root=opt.data_root,
            train=False,
            seq_len=opt.n_eval,
            image_size=opt.image_width)
    elif opt.dataset == 'kth':
        from data.kth import KTH
        train_data = KTH(
            train=True,
            data_root=opt.data_root,
            seq_len=opt.n_past + opt.n_future,
            image_size=opt.image_width)
        test_data = KTH(
            train=False,
            data_root=opt.data_root,
            seq_len=opt.n_eval,
            image_size=opt.image_width)
    elif opt.dataset == 'mcs':
        from data.mcs import MCS
        train_data = MCS(
            train=True,
            data_root=opt.data_root,
            seq_len=opt.n_past + opt.n_future,
            image_size=opt.image_width,
            task=opt.mcs_task,
            sequential=sequential,
            implausible=implausible,
            im_channels=opt.channels,
            use_edge_kernels=opt.use_edge_kernels,
            start_min=opt.start_min,
            start_max=opt.start_max,
            sequence_stride=opt.sequence_stride,
            reduce_static_frames=opt.reduce_static_frames,
            lifting_frame_index=opt.lifting_frame_index,)
        test_data = MCS(
            train=False,
            data_root=opt.data_root,
            seq_len=opt.n_eval,
            image_size=opt.image_width,
            task=opt.mcs_task,
            sequential=sequential,
            implausible=implausible,
            im_channels=opt.channels,
            use_edge_kernels=opt.use_edge_kernels,
            start_min=opt.start_min,
            start_max=opt.start_max,
            sequence_stride=opt.sequence_stride,
            reduce_static_frames=opt.reduce_static_frames,
            lifting_frame_index=opt.lifting_frame_index,)
    elif opt.dataset == 'mcs_test':
        from data.mcs import MCS
        train_data = MCS(
            train=True,
            data_root=opt.data_root,
            seq_len=opt.n_past + opt.n_future,
            image_size=opt.image_width,
            task=opt.mcs_task,
            sequential=sequential,
            implausible=implausible,
            test_set=True,
            im_channels=opt.channels,
            use_edge_kernels=opt.use_edge_kernels,
            start_min=opt.start_min,
            start_max=opt.start_max,
            sequence_stride=opt.sequence_stride,
            reduce_static_frames=opt.reduce_static_frames,
            lifting_frame_index=opt.lifting_frame_index,
            labels=True)
        test_data = MCS(
            train=False,
            data_root=opt.data_root,
            seq_len=opt.n_eval,
            image_size=opt.image_width,
            task=opt.mcs_task,
            sequential=sequential,
            implausible=implausible,
            test_set=True,
            im_channels=opt.channels,
            use_edge_kernels=opt.use_edge_kernels,
            start_min=opt.start_min,
            start_max=opt.start_max,
            sequence_stride=opt.sequence_stride,
            reduce_static_frames=opt.reduce_static_frames,
            lifting_frame_index=opt.lifting_frame_index,
            labels=True)

    return train_data, test_data


def sequence_input(seq, dtype):
    return [Variable(x.type(dtype)) for x in seq]


def normalize_data(opt, dtype, sequence):
    if opt.dataset == 'smmnist' or opt.dataset == 'kth' or opt.dataset == 'bair' or opt.dataset == 'mcs'\
            or opt.dataset == 'mcs_test':
        sequence.transpose_(0, 1)
        sequence.transpose_(3, 4).transpose_(2, 3)
    else:
        sequence.transpose_(0, 1)

    return sequence_input(sequence, dtype)


def is_sequence(arg):
    return (not hasattr(arg, "strip") and
            not type(arg) is np.ndarray and
            not hasattr(arg, "dot") and
            (hasattr(arg, "__getitem__") or
             hasattr(arg, "__iter__")))


def image_tensor(inputs, padding=1):
    # assert is_sequence(inputs)
    assert len(inputs) > 0
    # print(inputs)

    # if this is a list of lists, unpack them all and grid them up
    if is_sequence(inputs[0]) or (hasattr(inputs, "dim") and inputs.dim() > 4):
        images = [image_tensor(x) for x in inputs]
        if images[0].dim() == 3:
            c_dim = images[0].size(0)
            x_dim = images[0].size(1)
            y_dim = images[0].size(2)
        else:
            c_dim = 1
            x_dim = images[0].size(0)
            y_dim = images[0].size(1)

        result = torch.ones(c_dim,
                            x_dim * len(images) + padding * (len(images) - 1),
                            y_dim)
        for i, image in enumerate(images):
            result[:, i * x_dim + i * padding:
                      (i + 1) * x_dim + i * padding, :].copy_(image)

        return result

    # if this is just a list, make a stacked image
    else:
        images = [x.data if isinstance(x, torch.autograd.Variable) else x
                  for x in inputs]
        # print(images)
        if images[0].dim() == 3:
            c_dim = images[0].size(0)
            x_dim = images[0].size(1)
            y_dim = images[0].size(2)
        else:
            c_dim = 1
            x_dim = images[0].size(0)
            y_dim = images[0].size(1)

        result = torch.ones(c_dim,
                            x_dim,
                            y_dim * len(images) + padding * (len(images) - 1))
        for i, image in enumerate(images):
            result[:, :, i * y_dim + i * padding:
                         (i + 1) * y_dim + i * padding].copy_(image)
        return result


def save_np_img(fname, x):
    if x.shape[0] == 1:
        x = np.tile(x, (3, 1, 1))
    img = torch_tensor_to_img(x)
    img.save(fname)


def make_image(tensor):
    tensor = tensor.cpu().clamp(0, 1)
    if tensor.size(0) == 1:
        tensor = tensor.expand(3, tensor.size(1), tensor.size(2))
    # pdb.set_trace()
    return torch_tensor_to_img(tensor)


def draw_text_tensor(tensor, text):
    np_x = tensor.transpose(0, 1).transpose(1, 2).data.cpu().numpy()
    pil = Image.fromarray(np.uint8(np_x * 255))
    draw = ImageDraw.Draw(pil)
    draw.text((4, 64), text, (0, 0, 0))
    img = np.asarray(pil)
    return Variable(torch.Tensor(img / 255.)).transpose(1, 2).transpose(0, 1)


def save_gif(filename, inputs, duration=0.25):
    images = []
    for tensor in inputs:
        img = image_tensor(tensor, padding=0)
        img = img.cpu()
        img = img.transpose(0, 1).transpose(1, 2).clamp(0, 1)
        images.append((img.numpy() * 255).astype(np.uint8))
    imageio.mimsave(filename, images, duration=duration)


def save_gif_with_text(filename, inputs, text, duration=0.25):
    images = []
    for tensor, text in zip(inputs, text):
        img = image_tensor([draw_text_tensor(ti, texti) for ti, texti in zip(tensor, text)], padding=0)
        img = img.cpu()
        img = img.transpose(0, 1).transpose(1, 2).clamp(0, 1).numpy()
        images.append(img)
    imageio.mimsave(filename, images, duration=duration)


def save_image(filename, tensor):
    img = make_image(tensor)
    img.save(filename)


def save_tensors_image(filename, inputs, padding=1):
    images = image_tensor(inputs, padding)
    return save_image(filename, images)


def prod(l):
    return functools.reduce(lambda x, y: x * y, l)


def batch_flatten(x):
    return x.resize(x.size(0), prod(x.size()[1:]))


def clear_progressbar():
    # # moves up 3 lines
    # print("\033[2A")
    # # deletes the whole line, regardless of character position
    # print("\033[2K")
    # # moves up two lines again
    # print("\033[2A")
    print('\r')
    print(' ' * 80)
    print('\r')

def mse_metric(x1, x2):
    err = np.sum((x1 - x2) ** 2)
    err /= float(x1.shape[0] * x1.shape[1] * x1.shape[2])
    return err


def eval_seq(gt, pred):
    T = len(gt)
    bs = gt[0].shape[0]
    ssim = np.zeros((bs, T))
    psnr = np.zeros((bs, T))
    mse = np.zeros((bs, T))
    for i in range(bs):
        for t in range(T):
            for c in range(gt[t][i].shape[0]):
                ssim[i, t] += ssim_metric(gt[t][i][c], pred[t][i][c])
                psnr[i, t] += psnr_metric(gt[t][i][c], pred[t][i][c])
            ssim[i, t] /= gt[t][i].shape[0]
            psnr[i, t] /= gt[t][i].shape[0]
            mse[i, t] = mse_metric(gt[t][i], pred[t][i])

    return mse, ssim, psnr


# ssim function used in Babaeizadeh et al. (2017), Fin et al. (2016), etc.
def finn_eval_seq(gt, pred):
    T = len(gt)
    bs = gt[0].shape[0]
    ssim = np.zeros((bs, T))
    psnr = np.zeros((bs, T))
    mse = np.zeros((bs, T))
    for i in range(bs):
        for t in range(T):
            for c in range(gt[t][i].shape[0]):
                res = finn_ssim(gt[t][i][c], pred[t][i][c]).mean()
                if math.isnan(res):
                    ssim[i, t] += -1
                else:
                    ssim[i, t] += res
                psnr[i, t] += finn_psnr(gt[t][i][c], pred[t][i][c])
            ssim[i, t] /= gt[t][i].shape[0]
            psnr[i, t] /= gt[t][i].shape[0]
            mse[i, t] = mse_metric(gt[t][i], pred[t][i])

    return mse, ssim, psnr


def finn_psnr(x, y):
    mse = ((x - y) ** 2).mean()
    return 10 * np.log(1 / mse) / np.log(10)


def gaussian2(size, sigma):
    A = 1 / (2.0 * np.pi * sigma ** 2)
    x, y = np.mgrid[-size // 2 + 1:size // 2 + 1, -size // 2 + 1:size // 2 + 1]
    g = A * np.exp(-((x ** 2 / (2.0 * sigma ** 2)) + (y ** 2 / (2.0 * sigma ** 2))))
    return g


def fspecial_gauss(size, sigma):
    x, y = np.mgrid[-size // 2 + 1:size // 2 + 1, -size // 2 + 1:size // 2 + 1]
    g = np.exp(-((x ** 2 + y ** 2) / (2.0 * sigma ** 2)))
    return g / g.sum()


def finn_ssim(img1, img2, cs_map=False):
    img1 = img1.astype(np.float64)
    img2 = img2.astype(np.float64)
    size = 11
    sigma = 1.5
    window = fspecial_gauss(size, sigma)
    K1 = 0.01
    K2 = 0.03
    L = 1  # bitdepth of image
    C1 = (K1 * L) ** 2
    C2 = (K2 * L) ** 2
    mu1 = signal.fftconvolve(img1, window, mode='valid')
    mu2 = signal.fftconvolve(img2, window, mode='valid')
    mu1_sq = mu1 * mu1
    mu2_sq = mu2 * mu2
    mu1_mu2 = mu1 * mu2
    sigma1_sq = signal.fftconvolve(img1 * img1, window, mode='valid') - mu1_sq
    sigma2_sq = signal.fftconvolve(img2 * img2, window, mode='valid') - mu2_sq
    sigma12 = signal.fftconvolve(img1 * img2, window, mode='valid') - mu1_mu2
    if cs_map:
        return (((2 * mu1_mu2 + C1) * (2 * sigma12 + C2)) / ((mu1_sq + mu2_sq + C1) *
                                                             (sigma1_sq + sigma2_sq + C2)),
                (2.0 * sigma12 + C2) / (sigma1_sq + sigma2_sq + C2))
    else:
        return ((2 * mu1_mu2 + C1) * (2 * sigma12 + C2)) / ((mu1_sq + mu2_sq + C1) *
                                                            (sigma1_sq + sigma2_sq + C2))


def init_weights(m):
    classname = m.__class__.__name__
    if classname.find('Conv') != -1 or classname.find('Linear') != -1:
        m.weight.data.normal_(0.0, 0.02)
        m.bias.data.fill_(0)
    elif classname.find('BatchNorm') != -1:
        m.weight.data.normal_(1.0, 0.02)
        m.bias.data.fill_(0)
