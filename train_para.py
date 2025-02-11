"""Train a model on SQuAD.

Author:
    Chris Chute (chute@stanford.edu)
"""

import numpy as np
import random
import torch
import torch.nn as nn
import torch.nn.functional as F
import torch.optim as optim
import torch.optim.lr_scheduler as sched
import torch.utils.data as data
import util

from args import get_train_args
from collections import OrderedDict
from json import dumps
from models import BiDAF
from tensorboardX import SummaryWriter
from tqdm import tqdm
from ujson import load as json_load
from util import collate_fn_para, SQuAD_paraphrase

from model_para import Paraphraser
from pprint import pprint as pp
from setup import load

def main(args):
    # Set up logging and devices (unchanged from train.py)
    args.save_dir = util.get_save_dir(args.save_dir, args.name, training=True)
    log = util.get_logger(args.save_dir, args.name)
    tbx = SummaryWriter(args.save_dir)                  # train only, not in test
    device, args.gpu_ids = util.get_available_devices() # todo(small): should this be args (compare test_para)
    log.info(f'Args: {dumps(vars(args), indent=4, sort_keys=True)}')
    args.batch_size *= max(1, len(args.gpu_ids))        # args.py: default size is 64

    # Set random seed (unchanged) - train only
    log.info(f'Using random seed {args.seed}...')
    random.seed(args.seed)
    np.random.seed(args.seed)
    torch.manual_seed(args.seed)
    torch.cuda.manual_seed_all(args.seed)

    # Get embeddings
    log.info('Loading embeddings...')
    word_vectors = util.torch_from_json(args.word_emb_file)

    # Prepare BiDAF model (must already trained)
    log.info('Building BiDAF model (should be pretrained)')
    bidaf_model = BiDAF(word_vectors=word_vectors,          # todo: these word vectors shouldn't matter?
                          hidden_size=args.hidden_size)     # since they will be loaded in during load_model?
                          #drop_prob=args.drop_prob)        # no drop probability since we are not training
    bidaf_model = nn.DataParallel(bidaf_model, args.gpu_ids)

    if args.short_test:
        args.hidden_size = 5
    elif not args.load_path:
        log.info("Trying to trian paraphraser withou bidaf model. "
                 "First train BiDAF and then specify the load path. Exiting")
        exit(1)
    else:
        log.info(f'Loading checkpoint from {args.load_path}...')
        bidaf_model = util.load_model(bidaf_model, args.load_path, args.gpu_ids, return_step=False) # don't need step since we aren't training
        bidaf_model = bidaf_model.to(device)
        bidaf_model.eval()                  # we eval only (vs train)

    # todo: Setup the Paraphraser model
    paraphaser_model = Paraphraser(word_vectors=word_vectors,
                                   hidden_size=args.hidden_size,
                                   drop_prob=args.drop_prob)


    # Get data loader
    log.info('Building dataset...')
    # New for paraphrase: squad_paraphrase has extra fields
    train_dataset = SQuAD_paraphrase(args.train_record_file, args.use_squad_v2)    # train.npz (from setup.py, build_features())
    train_loader = data.DataLoader(train_dataset,                       # this dataloader used for all epoch iteration
                                   batch_size=args.batch_size,
                                   shuffle=True,
                                   num_workers=args.num_workers,
                                   collate_fn=collate_fn_para)
    dev_dataset = SQuAD_paraphrase(args.dev_record_file, args.use_squad_v2)        # dev.npz (same as above)
    dev_loader = data.DataLoader(dev_dataset,                           # dev.npz used in evaluate() fcn
                                 batch_size=args.batch_size,
                                 shuffle=False,
                                 num_workers=args.num_workers,
                                 collate_fn=collate_fn_para)

    # todo: this is just for looking at the paraphrases
    idx2word_dict = load(args.idx2word_file)

    #Get saver
    # saver = util.CheckpointSaver(args.save_dir,
    #                              max_checkpoints=args.max_checkpoints,
    #                              metric_name=args.metric_name,
    #                              maximize_metric=args.maximize_metric,
    #                              log=log)

    #Get optimizer and scheduler
    # ema = util.EMA(paraphaser_model, args.ema_decay)
    # optimizer = optim.Adadelta(paraphaser_model.parameters(), args.lr,
    #                            weight_decay=args.l2_wd)
    # scheduler = sched.LambdaLR(optimizer, lambda s: 1.)  # Constant LR
    # Train
    step = 0
    log.info('Training...')
    steps_till_eval = args.eval_steps
    epoch = step // len(train_dataset)


    while epoch != args.num_epochs:
        epoch += 1
        log.info(f'Starting epoch {epoch}...')
        with torch.enable_grad(), \
                tqdm(total=len(train_loader.dataset)) as progress_bar:
            for cw_idxs, cc_idxs, qw_idxs, qc_idxs, y1, y2, cphr_idxs, qphr_idxs, qphr_types, ids in train_loader:
                # Setup for forward
                # note that cc_idxs, qc_idxs are not used! (character indices)
                cw_idxs = cw_idxs.to(device)        # todo what does this actually do
                qw_idxs = qw_idxs.to(device)

                cphr_idxs = cphr_idxs.to(device)
                qphr_idxs = qphr_idxs.to(device)
                qphr_types = qphr_types.to(device)

                batch_size = cw_idxs.size(0)
                # if args.short_test:
                #     print(f'batch size: {batch_size}')
                #     for i, type in enumerate(cphr_idxs[0]):
                #         print(f'type: {i}')
                #         pp(type)
                #     for x in (qphr_idxs[0], qphr_types[0]):
                #         pp(x)
                #     return

                paraphrased = paraphaser_model(qphr_idxs, qphr_types, cphr_idxs)
                for idx, p in enumerate(paraphrased):   # enumerate over batch_size
                    non_zeros = p[p.nonzero()].squeeze()
                    #paraphrased[idx] = non_zeros
                    sentence_as_list = [idx2word_dict[str(w.item())] for w in non_zeros]
                    pp(" ".join(sentence_as_list))
                    #pp([idx2word_dict[w] for w in non_zeros])

                if args.short_test:
                    return

                optimizer.zero_grad()

                # Forward
                log_p1, log_p2 = model(cw_idxs, qw_idxs)
                y1, y2 = y1.to(device), y2.to(device)
                loss = F.nll_loss(log_p1, y1) + F.nll_loss(log_p2, y2)
                loss_val = loss.item()

                # Backward
                loss.backward()
                nn.utils.clip_grad_norm_(model.parameters(), args.max_grad_norm)
                optimizer.step()
                scheduler.step(step // batch_size)      # // is floor division
                ema(model, step // batch_size)

                # Log info
                step += batch_size
                progress_bar.update(batch_size)
                progress_bar.set_postfix(epoch=epoch,
                                         NLL=loss_val)
                tbx.add_scalar('train/NLL', loss_val, step)
                tbx.add_scalar('train/LR',
                               optimizer.param_groups[0]['lr'],
                               step)

                steps_till_eval -= batch_size
                if steps_till_eval <= 0:
                    steps_till_eval = args.eval_steps

                    # Evaluate and save checkpoint
                    log.info(f'Evaluating at step {step}...')
                    ema.assign(model)
                    results, pred_dict = evaluate(model, dev_loader, device,    # call eval with dev_loader
                                                  args.dev_eval_file,
                                                  args.max_ans_len,
                                                  args.use_squad_v2)
                    saver.save(step, model, results[args.metric_name], device)
                    ema.resume(model)

                    # Log to console
                    results_str = ', '.join(f'{k}: {v:05.2f}' for k, v in results.items())
                    log.info(f'Dev {results_str}')

                    # Log to TensorBoard
                    log.info('Visualizing in TensorBoard...')
                    for k, v in results.items():
                        tbx.add_scalar(f'dev/{k}', v, step)
                    util.visualize(tbx,
                                   pred_dict=pred_dict,
                                   eval_path=args.dev_eval_file,
                                   step=step,
                                   split='dev',
                                   num_visuals=args.num_visuals)


def evaluate(model, data_loader, device, eval_file, max_len, use_squad_v2):
    nll_meter = util.AverageMeter()

    model.eval()        # put model in eval mode
    pred_dict = {}
    with open(eval_file, 'r') as fh:
        gold_dict = json_load(fh)
    with torch.no_grad(), \
            tqdm(total=len(data_loader.dataset)) as progress_bar:
        for cw_idxs, cc_idxs, qw_idxs, qc_idxs, y1, y2, ids in data_loader:
            # Setup for forward
            cw_idxs = cw_idxs.to(device)
            qw_idxs = qw_idxs.to(device)
            batch_size = cw_idxs.size(0)

            # Forward
            log_p1, log_p2 = model(cw_idxs, qw_idxs)
            y1, y2 = y1.to(device), y2.to(device)
            loss = F.nll_loss(log_p1, y1) + F.nll_loss(log_p2, y2)
            nll_meter.update(loss.item(), batch_size)

            # Get F1 and EM scores
            p1, p2 = log_p1.exp(), log_p2.exp()
            starts, ends = util.discretize(p1, p2, max_len, use_squad_v2)

            # Log info
            progress_bar.update(batch_size)
            progress_bar.set_postfix(NLL=nll_meter.avg)

            preds, _ = util.convert_tokens(gold_dict,
                                           ids.tolist(),
                                           starts.tolist(),
                                           ends.tolist(),
                                           use_squad_v2)
            pred_dict.update(preds)

    model.train()       # put model back in train mode

    results = util.eval_dicts(gold_dict, pred_dict, use_squad_v2)
    results_list = [('NLL', nll_meter.avg),
                    ('F1', results['F1']),
                    ('EM', results['EM'])]
    if use_squad_v2:
        results_list.append(('AvNA', results['AvNA']))
    results = OrderedDict(results_list)

    return results, pred_dict


if __name__ == '__main__':
    main(get_train_args())
