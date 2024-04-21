import json
import os
import platform
import shutil
import time
from datetime import timedelta

import paddle
import yaml
from paddle import summary
from paddle.distributed import fleet
from paddle.io import DataLoader, DistributedBatchSampler
from paddle.metric import accuracy
from paddle.optimizer.lr import CosineAnnealingDecay
from sklearn.metrics import confusion_matrix
from tqdm import tqdm
from visualdl import LogWriter

from ppacls import SUPPORT_MODEL, __version__
from ppacls.data_utils.collate_fn import collate_fn
from ppacls.data_utils.featurizer import AudioFeaturizer
from ppacls.data_utils.reader import CustomDataset
from ppacls.data_utils.spec_aug import SpecAug
from ppacls.models.campplus import CAMPPlus
from ppacls.models.ecapa_tdnn import EcapaTdnn
from ppacls.models.eres2net import ERes2Net
from ppacls.models.panns import PANNS_CNN6, PANNS_CNN10, PANNS_CNN14
from ppacls.models.res2net import Res2Net
from ppacls.models.resnet_se import ResNetSE
from ppacls.models.tdnn import TDNN
from ppacls.utils.logger import setup_logger
from ppacls.utils.scheduler import cosine_decay_with_warmup
from ppacls.utils.utils import dict_to_object, plot_confusion_matrix, print_arguments

logger = setup_logger(__name__)


class PPAClsTrainer(object):
    def __init__(self, configs, use_gpu=True):
        """ ppacls集成工具类

        :param configs: 配置字典
        :param use_gpu: 是否使用GPU训练模型
        """
        if use_gpu:
            assert paddle.is_compiled_with_cuda(), 'GPU不可用'
            paddle.device.set_device("gpu")
        else:
            os.environ['CUDA_VISIBLE_DEVICES'] = '-1'
            paddle.device.set_device("cpu")
        self.use_gpu = use_gpu
        # 读取配置文件
        if isinstance(configs, str):
            with open(configs, 'r', encoding='utf-8') as f:
                configs = yaml.load(f.read(), Loader=yaml.FullLoader)
            print_arguments(configs=configs)
        self.configs = dict_to_object(configs)
        assert self.configs.use_model in SUPPORT_MODEL, f'没有该模型：{self.configs.use_model}'
        self.model = None
        self.test_loader = None
        self.amp_scaler = None
        # 获取分类标签
        with open(self.configs.dataset_conf.label_list_path, 'r', encoding='utf-8') as f:
            lines = f.readlines()
        self.class_labels = [l.replace('\n', '') for l in lines]
        # 获取特征器
        self.audio_featurizer = AudioFeaturizer(feature_method=self.configs.preprocess_conf.feature_method,
                                                method_args=self.configs.preprocess_conf.get('method_args', {}))
        self.spec_aug = SpecAug(**self.configs.dataset_conf.get('spec_aug_args', {}))
        if platform.system().lower() == 'windows':
            self.configs.dataset_conf.dataLoader.num_workers = 0
            logger.warning('Windows系统不支持多线程读取数据，已自动关闭！')
        self.max_step, self.train_step = None, None
        self.train_loss, self.train_acc = None, None
        self.train_eta_sec = None
        self.eval_loss, self.eval_acc = None, None
        self.test_log_step, self.train_log_step = 0, 0
        self.stop_train, self.stop_eval = False, False

    def __setup_dataloader(self, is_train=False):
        if is_train:
            self.train_dataset = CustomDataset(data_list_path=self.configs.dataset_conf.train_list,
                                               do_vad=self.configs.dataset_conf.do_vad,
                                               max_duration=self.configs.dataset_conf.max_duration,
                                               min_duration=self.configs.dataset_conf.min_duration,
                                               aug_conf=self.configs.dataset_conf.aug_conf,
                                               sample_rate=self.configs.dataset_conf.sample_rate,
                                               use_dB_normalization=self.configs.dataset_conf.use_dB_normalization,
                                               target_dB=self.configs.dataset_conf.target_dB,
                                               mode='train')
            # 设置支持多卡训练
            train_sampler = None
            if paddle.distributed.get_world_size() > 1:
                # 设置支持多卡训练
                train_sampler = DistributedBatchSampler(dataset=self.train_dataset,
                                                        batch_size=self.configs.dataset_conf.dataLoader.batch_size,
                                                        shuffle=True)
            self.train_loader = DataLoader(dataset=self.train_dataset,
                                           collate_fn=collate_fn,
                                           shuffle=(train_sampler is None),
                                           batch_sampler=train_sampler,
                                           **self.configs.dataset_conf.dataLoader)
        # 获取测试数据
        self.test_dataset = CustomDataset(data_list_path=self.configs.dataset_conf.test_list,
                                          do_vad=self.configs.dataset_conf.do_vad,
                                          max_duration=self.configs.dataset_conf.eval_conf.max_duration,
                                          min_duration=self.configs.dataset_conf.min_duration,
                                          sample_rate=self.configs.dataset_conf.sample_rate,
                                          use_dB_normalization=self.configs.dataset_conf.use_dB_normalization,
                                          target_dB=self.configs.dataset_conf.target_dB,
                                          mode='eval')
        self.test_loader = DataLoader(dataset=self.test_dataset,
                                      collate_fn=collate_fn,
                                      shuffle=True,
                                      batch_size=self.configs.dataset_conf.eval_conf.batch_size,
                                      num_workers=self.configs.dataset_conf.dataLoader.num_workers)

    def __setup_model(self, input_size, is_train=False):
        # 自动获取列表数量
        if self.configs.model_conf.num_class is None:
            self.configs.model_conf.num_class = len(self.class_labels)
        # 获取模型
        if self.configs.use_model == 'EcapaTdnn':
            self.model = EcapaTdnn(input_size=input_size, **self.configs.model_conf)
        elif self.configs.use_model == 'PANNS_CNN6':
            self.model = PANNS_CNN6(input_size=input_size, **self.configs.model_conf)
        elif self.configs.use_model == 'PANNS_CNN10':
            self.model = PANNS_CNN10(input_size=input_size, **self.configs.model_conf)
        elif self.configs.use_model == 'PANNS_CNN14':
            self.model = PANNS_CNN14(input_size=input_size, **self.configs.model_conf)
        elif self.configs.use_model == 'Res2Net':
            self.model = Res2Net(input_size=input_size, **self.configs.model_conf)
        elif self.configs.use_model == 'ResNetSE':
            self.model = ResNetSE(input_size=input_size, **self.configs.model_conf)
        elif self.configs.use_model == 'TDNN':
            self.model = TDNN(input_size=input_size, **self.configs.model_conf)
        elif self.configs.use_model == 'ERes2Net':
            self.model = ERes2Net(input_size=input_size, **self.configs.model_conf)
        elif self.configs.use_model == 'CAMPPlus':
            self.model = CAMPPlus(input_size=input_size, **self.configs.model_conf)
        else:
            raise Exception(f'{self.configs.use_model} 模型不存在！')
        summary(self.model, (1, 98, self.audio_featurizer.feature_dim))
        # print(self.model)
        # 获取损失函数
        weight = paddle.to_tensor(self.configs.train_conf.loss_weight, dtype=paddle.float32) \
            if self.configs.train_conf.loss_weight is not None else None
        self.loss = paddle.nn.CrossEntropyLoss(weight=weight)
        if is_train:
            if self.configs.train_conf.enable_amp:
                # 自动混合精度训练，逻辑2，定义GradScaler
                self.amp_scaler = paddle.amp.GradScaler(init_loss_scaling=1024)
            # 学习率衰减函数
            scheduler_args = self.configs.optimizer_conf.get('scheduler_args', {}) \
                if self.configs.optimizer_conf.get('scheduler_args', {}) is not None else {}
            if self.configs.optimizer_conf.scheduler == 'CosineAnnealingLR':
                max_step = int(self.configs.train_conf.max_epoch * 1.2) * len(self.train_loader)
                self.scheduler = CosineAnnealingDecay(T_max=max_step,
                                                      **scheduler_args)
            elif self.configs.optimizer_conf.scheduler == 'WarmupCosineSchedulerLR':
                self.scheduler = cosine_decay_with_warmup(step_per_epoch=len(self.train_loader),
                                                          **scheduler_args)
            else:
                raise Exception(f'不支持学习率衰减函数：{self.configs.optimizer_conf.scheduler}')
            # 获取优化方法
            optimizer = self.configs.optimizer_conf.optimizer
            if optimizer == 'Adam':
                self.optimizer = paddle.optimizer.Adam(parameters=self.model.parameters(),
                                                       learning_rate=self.scheduler,
                                                       weight_decay=self.configs.optimizer_conf.weight_decay)
            elif optimizer == 'AdamW':
                self.optimizer = paddle.optimizer.AdamW(parameters=self.model.parameters(),
                                                        learning_rate=self.scheduler,
                                                        weight_decay=self.configs.optimizer_conf.weight_decay)
            elif optimizer == 'Momentum':
                self.optimizer = paddle.optimizer.Momentum(parameters=self.model.parameters(),
                                                           momentum=self.configs.optimizer_conf.get('momentum', 0.9),
                                                           learning_rate=self.scheduler,
                                                           weight_decay=self.configs.optimizer_conf.weight_decay)
            else:
                raise Exception(f'不支持优化方法：{optimizer}')

    def __load_pretrained(self, pretrained_model):
        # 加载预训练模型
        if pretrained_model is not None:
            if os.path.isdir(pretrained_model):
                pretrained_model = os.path.join(pretrained_model, 'model.pdparams')
            assert os.path.exists(pretrained_model), f"{pretrained_model} 模型不存在！"
            model_dict = self.model.state_dict()
            model_state_dict = paddle.load(pretrained_model)
            # 过滤不存在的参数
            for name, weight in model_dict.items():
                if name in model_state_dict.keys():
                    if list(weight.shape) != list(model_state_dict[name].shape):
                        logger.warning('{} not used, shape {} unmatched with {} in model.'.
                                       format(name, list(model_state_dict[name].shape), list(weight.shape)))
                        model_state_dict.pop(name, None)
                else:
                    logger.warning('Lack weight: {}'.format(name))
            self.model.set_state_dict(model_state_dict)
            logger.info('成功加载预训练模型：{}'.format(pretrained_model))

    def __load_checkpoint(self, save_model_path, resume_model):
        last_epoch = -1
        best_acc = 0
        last_model_dir = os.path.join(save_model_path,
                                      f'{self.configs.use_model}_{self.configs.preprocess_conf.feature_method}',
                                      'last_model')
        if resume_model is not None or (os.path.exists(os.path.join(last_model_dir, 'model.pdparams'))
                                        and os.path.exists(os.path.join(last_model_dir, 'optimizer.pdopt'))):
            # 自动获取最新保存的模型
            if resume_model is None: resume_model = last_model_dir
            assert os.path.exists(os.path.join(resume_model, 'model.pdparams')), "模型参数文件不存在！"
            assert os.path.exists(os.path.join(resume_model, 'optimizer.pdopt')), "优化方法参数文件不存在！"
            self.model.set_state_dict(paddle.load(os.path.join(resume_model, 'model.pdparams')))
            self.optimizer.set_state_dict(paddle.load(os.path.join(resume_model, 'optimizer.pdopt')))
            # 自动混合精度参数
            if self.amp_scaler is not None and os.path.exists(os.path.join(resume_model, 'scaler.pdparams')):
                self.amp_scaler.load_state_dict(paddle.load(os.path.join(resume_model, 'scaler.pdparams')))
            with open(os.path.join(resume_model, 'model.state'), 'r', encoding='utf-8') as f:
                json_data = json.load(f)
                last_epoch = json_data['last_epoch'] - 1
                best_acc = json_data['accuracy']
            logger.info('成功恢复模型参数和优化方法参数：{}'.format(resume_model))
        return last_epoch, best_acc

    # 保存模型
    def __save_checkpoint(self, save_model_path, epoch_id, best_acc=0., best_model=False):
        if best_model:
            model_path = os.path.join(save_model_path,
                                      f'{self.configs.use_model}_{self.configs.preprocess_conf.feature_method}',
                                      'best_model')
        else:
            model_path = os.path.join(save_model_path,
                                      f'{self.configs.use_model}_{self.configs.preprocess_conf.feature_method}',
                                      'epoch_{}'.format(epoch_id))
        os.makedirs(model_path, exist_ok=True)
        try:
            paddle.save(self.optimizer.state_dict(), os.path.join(model_path, 'optimizer.pdopt'))
            paddle.save(self.model.state_dict(), os.path.join(model_path, 'model.pdparams'))
            # 自动混合精度参数
            if self.amp_scaler is not None:
                paddle.save(self.amp_scaler.state_dict(), os.path.join(model_path, 'scaler.pdparams'))
        except Exception as e:
            logger.error(f'保存模型时出现错误，错误信息：{e}')
            return
        with open(os.path.join(model_path, 'model.state'), 'w', encoding='utf-8') as f:
            data = {"last_epoch": epoch_id, "accuracy": best_acc, "version": __version__}
            f.write(json.dumps(data))
        if not best_model:
            last_model_path = os.path.join(save_model_path,
                                           f'{self.configs.use_model}_{self.configs.preprocess_conf.feature_method}',
                                           'last_model')
            shutil.rmtree(last_model_path, ignore_errors=True)
            shutil.copytree(model_path, last_model_path)
            # 删除旧的模型
            old_model_path = os.path.join(save_model_path,
                                          f'{self.configs.use_model}_{self.configs.preprocess_conf.feature_method}',
                                          'epoch_{}'.format(epoch_id - 3))
            if os.path.exists(old_model_path):
                shutil.rmtree(old_model_path)
        logger.info('已保存模型：{}'.format(model_path))

    def __train_epoch(self, epoch_id, local_rank, writer):
        train_times, accuracies, loss_sum = [], [], []
        start = time.time()
        for batch_id, (audio, label, input_lens_ratio) in enumerate(self.train_loader()):
            if self.stop_train: break
            features, _ = self.audio_featurizer(audio, input_lens_ratio)
            # 特征增强
            if self.configs.dataset_conf.use_spec_aug:
                features = self.spec_aug(features)
            # 执行模型计算，是否开启自动混合精度
            with paddle.amp.auto_cast(enable=self.configs.train_conf.enable_amp, level='O1'):
                if self.configs.use_model == 'EcapaTdnn':
                    output = self.model([features, input_lens_ratio])
                else:
                    output = self.model(features)
            # 计算损失值
            los = self.loss(output, label)
            # 是否开启自动混合精度
            if self.configs.train_conf.enable_amp:
                # loss缩放，乘以系数loss_scaling
                scaled = self.amp_scaler.scale(los)
                scaled.backward()
            else:
                los.backward()
            # 是否开启自动混合精度
            if self.configs.train_conf.enable_amp:
                # 更新参数（参数梯度先除系数loss_scaling再更新参数）
                self.amp_scaler.step(self.optimizer)
                # 基于动态loss_scaling策略更新loss_scaling系数
                self.amp_scaler.update()
            else:
                self.optimizer.step()
            self.optimizer.clear_grad()
            # 计算准确率
            label = paddle.reshape(label, shape=(-1, 1))
            acc = accuracy(input=paddle.nn.functional.softmax(output), label=label)
            accuracies.append(float(acc))
            loss_sum.append(float(los))
            train_times.append((time.time() - start) * 1000)
            self.train_step += 1

            # 多卡训练只使用一个进程打印
            if batch_id % self.configs.train_conf.log_interval == 0 and local_rank == 0:
                batch_id = batch_id + 1
                # 计算每秒训练数据量
                train_speed = self.configs.dataset_conf.dataLoader.batch_size / (sum(train_times) / len(train_times) / 1000)
                # 计算剩余时间
                self.train_eta_sec = (sum(train_times) / len(train_times)) * (self.max_step - self.train_step) / 1000
                eta_str = str(timedelta(seconds=int(self.train_eta_sec)))
                self.train_loss = sum(loss_sum) / len(loss_sum)
                self.train_acc = sum(accuracies) / len(accuracies)
                logger.info(f'Train epoch: [{epoch_id}/{self.configs.train_conf.max_epoch}], '
                            f'batch: [{batch_id}/{len(self.train_loader)}], '
                            f'loss: {self.train_loss:.5f}, accuracy: {self.train_acc:.5f}, '
                            f'learning rate: {self.scheduler.get_lr():>.8f}, '
                            f'speed: {train_speed:.2f} data/sec, eta: {eta_str}')
                writer.add_scalar('Train/Loss', self.train_loss, self.train_log_step)
                writer.add_scalar('Train/Accuracy', self.train_acc, self.train_log_step)
                # 记录学习率
                writer.add_scalar('Train/lr', self.scheduler.get_lr(), self.train_log_step)
                train_times, accuracies, loss_sum = [], [], []
                self.train_log_step += 1
            self.scheduler.step()
            start = time.time()

    def train(self,
              save_model_path='models/',
              resume_model=None,
              pretrained_model=None):
        """
        训练模型
        :param save_model_path: 模型保存的路径
        :param resume_model: 恢复训练，当为None则不使用预训练模型
        :param pretrained_model: 预训练模型的路径，当为None则不使用预训练模型
        """
        paddle.seed(1000)
        # 获取有多少张显卡训练
        nranks = paddle.distributed.get_world_size()
        local_rank = paddle.distributed.get_rank()
        writer = None
        if local_rank == 0:
            # 日志记录器
            writer = LogWriter(logdir='log')

        if nranks > 1 and self.use_gpu:
            # 初始化Fleet环境
            strategy = fleet.DistributedStrategy()
            fleet.init(is_collective=True, strategy=strategy)

        # 获取数据
        self.__setup_dataloader(is_train=True)
        # 获取模型
        self.__setup_model(input_size=self.audio_featurizer.feature_dim, is_train=True)

        # 支持多卡训练
        if nranks > 1 and self.use_gpu:
            self.optimizer = fleet.distributed_optimizer(self.optimizer)
            self.model = fleet.distributed_model(self.model)
        logger.info('训练数据：{}'.format(len(self.train_dataset)))

        self.__load_pretrained(pretrained_model=pretrained_model)
        # 加载恢复模型
        last_epoch, best_acc = self.__load_checkpoint(save_model_path=save_model_path, resume_model=resume_model)

        self.train_loss, self.train_acc = None, None
        self.eval_loss, self.eval_acc = None, None
        self.test_log_step, self.train_log_step = 0, 0
        last_epoch += 1
        if local_rank == 0:
            writer.add_scalar('Train/lr', self.scheduler.get_lr(), last_epoch)
        # 最大步数
        self.max_step = len(self.train_loader) * self.configs.train_conf.max_epoch
        self.train_step = max(last_epoch, 0) * len(self.train_loader)
        # 开始训练
        for epoch_id in range(last_epoch, self.configs.train_conf.max_epoch):
            if self.stop_train: break
            epoch_id += 1
            start_epoch = time.time()
            # 训练一个epoch
            self.__train_epoch(epoch_id=epoch_id, local_rank=local_rank, writer=writer)
            # 多卡训练只使用一个进程执行评估和保存模型
            if local_rank == 0:
                logger.info('=' * 70)
                self.eval_loss, self.eval_acc = self.evaluate()
                logger.info('Test epoch: {}, time/epoch: {}, loss: {:.5f}, accuracy: {:.5f}'.format(
                    epoch_id, str(timedelta(seconds=(time.time() - start_epoch))), self.eval_loss, self.eval_acc))
                logger.info('=' * 70)
                writer.add_scalar('Test/Accuracy', self.eval_acc, self.test_log_step)
                writer.add_scalar('Test/Loss', self.eval_loss, self.test_log_step)
                self.test_log_step += 1
                self.model.train()
                # 保存最优模型
                if self.eval_acc >= best_acc:
                    best_acc = self.eval_acc
                    self.__save_checkpoint(save_model_path=save_model_path, epoch_id=epoch_id, best_acc=self.eval_acc,
                                           best_model=True)
                # 保存模型
                self.__save_checkpoint(save_model_path=save_model_path, epoch_id=epoch_id, best_acc=self.eval_acc)

    def evaluate(self, resume_model=None, save_matrix_path=None):
        """
        评估模型
        :param resume_model: 所使用的模型
        :param save_matrix_path: 保存混合矩阵的路径
        :return: 评估结果
        """
        if self.test_loader is None:
            self.__setup_dataloader()
        if self.model is None:
            self.__setup_model(input_size=self.audio_featurizer.feature_dim)
        if resume_model is not None:
            if os.path.isdir(resume_model):
                resume_model = os.path.join(resume_model, 'model.pdparams')
            assert os.path.exists(resume_model), f"{resume_model} 模型不存在！"
            model_state_dict = paddle.load(resume_model)
            self.model.set_state_dict(model_state_dict)
            logger.info(f'成功加载模型：{resume_model}')
        self.model.eval()
        if isinstance(self.model, paddle.DataParallel):
            eval_model = self.model._layers
        else:
            eval_model = self.model

        accuracies, losses, preds, labels = [], [], [], []
        with paddle.no_grad():
            for batch_id, (audio, label, input_lens_ratio) in enumerate(tqdm(self.test_loader())):
                if self.stop_eval: break
                features, _ = self.audio_featurizer(audio, input_lens_ratio)
                if self.configs.use_model == 'EcapaTdnn':
                    output = eval_model([features, input_lens_ratio])
                else:
                    output = eval_model(features)
                los = self.loss(output, label)
                # 计算准确率
                label = paddle.reshape(label, shape=(-1, 1))
                acc = accuracy(input=paddle.nn.functional.softmax(output), label=label)
                accuracies.append(float(acc))
                losses.append(float(los))
                # 模型预测标签
                pred = paddle.argsort(output, descending=True)[:, 0].numpy().tolist()
                preds.extend(pred)
                # 真实标签
                labels.extend(label.numpy().tolist())
        loss = float(sum(losses) / len(losses)) if len(losses) > 0 else -1
        acc = float(sum(accuracies) / len(accuracies)) if len(accuracies) > 0 else -1
        # 保存混合矩阵
        if save_matrix_path is not None:
            try:
                cm = confusion_matrix(labels, preds)
                plot_confusion_matrix(cm=cm, save_path=os.path.join(save_matrix_path, f'{int(time.time())}.png'),
                                      class_labels=self.class_labels)
            except Exception as e:
                logger.error(f'保存混淆矩阵失败：{e}')
        self.model.train()
        return loss, acc

    def export(self, save_model_path='models/', resume_model='models/EcapaTdnn_Fbank/best_model/'):
        """
        导出预测模型
        :param save_model_path: 模型保存的路径
        :param resume_model: 准备转换的模型路径
        :return:
        """
        # 获取模型
        self.__setup_model(input_size=self.audio_featurizer.feature_dim)
        # 加载预训练模型
        if os.path.isdir(resume_model):
            resume_model = os.path.join(resume_model, 'model.pdparams')
        assert os.path.exists(resume_model), f"{resume_model} 模型不存在！"
        model_state_dict = paddle.load(resume_model)
        self.model.set_state_dict(model_state_dict)
        logger.info('成功恢复模型参数和优化方法参数：{}'.format(resume_model))
        self.model.eval()
        # 获取静态模型
        infer_model = self.model.export()
        infer_model_dir = os.path.join(save_model_path,
                                       f'{self.configs.use_model}_{self.configs.preprocess_conf.feature_method}',
                                       'infer')
        os.makedirs(infer_model_dir, exist_ok=True)
        infer_model_path = os.path.join(infer_model_dir, 'model')
        paddle.jit.save(infer_model, infer_model_path)
        logger.info("预测模型已保存：{}".format(infer_model_path))
