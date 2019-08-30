import argparse
import torch
import torch.nn as nn
import torch.optim as optim
import time

from probing_models import LinearSST
from sst_dataset import SST
from torch.utils.data import DataLoader
from utilities import Visualizations, print_loss
from torch.nn.utils.rnn import pack_sequence, pad_sequence, pack_padded_sequence
from torchnlp.word_to_vector import GloVe


def get_args():
    """get input arguments"""
    parser = argparse.ArgumentParser(description="train")

    parser.add_argument('--model', default='', type=str)
    parser.add_argument('--epochs', default=60, type=int)
    parser.add_argument('--batch_size', default=16, type=int)
    parser.add_argument('--resume', default='', type=str)
    parser.add_argument('--save', default=False, type=bool)
    parser.add_argument('--lr', default=0.0001, type=float)
    parser.add_argument('--granularity', default=True, type=bool)
    parser.add_argument('--layer', default='1', type=int)

    return parser.parse_args()


class GLoVeCollate:
    def __init__(self):
        self.vectors = GloVe()
    def __call__(self, batch):
        # each element in "batch" is a dict {text:, label:}
        lengths = [len(x['text']) for x in batch]
        sentences = [self.vectors[x['text']] for x in batch]
        labels = [x['label'] for x in batch]

        return sentences, labels, lengths


def train(train_loader, model, criterion, optimizer, embedder, layer, granularity):
    running_loss = 0
    running_acc = 0
    iteration_count = 0
    num_samples = 0
    start = time.time()
    model.train()

    for idx, sample in enumerate(train_loader):
        sentences, labels, lengths = sample
        # Get activations per word
        word_batch = []
        word_labels = []
        for i in range(len(lengths)):
            for j in range(lengths[i]):
                word_batch.append(sentences[i][j])
                word_labels.append(labels[i])

        num_samples += len(word_labels)
        word_batch = torch.stack(word_batch, dim=0).cuda()
        labels = torch.LongTensor(word_labels).cuda()
        # zero gradients
        model.zero_grad()
        scores = model(word_batch)
        scores = scores.view(-1, granularity)
        labels = labels.view(-1)

        # get accuracy scores
        for idx, i in enumerate(scores):
            _, pos = i.max(0)
            if pos == labels[idx]:
                running_acc += 1

        loss = criterion(scores, labels)
        loss.backward()
        optimizer.step()

        running_loss += loss.item()
        iteration_count += 1

    train_time = time.time() - start
    accuracy = (running_acc / num_samples) * 100
    loss = running_loss / iteration_count

    return loss, accuracy, train_time


def test(test_loader, model, criterion, embedder, layer, granularity):
    running_loss = 0
    running_acc = 0
    iteration_count = 0
    num_samples = 0
    start = time.time()
    model.eval()

    with torch.no_grad():
        for idx, sample in enumerate(test_loader):
            sentences, labels, lengths = sample

            # Get activations per word
            word_batch = []
            word_labels = []
            for i in range(len(lengths)):
                for j in range(lengths[i]):
                    word_batch.append(sentences[i][j])
                    word_labels.append(labels[i])

            num_samples += len(word_labels)
            word_batch = torch.stack(word_batch, dim=0).cuda()
            labels = torch.LongTensor(word_labels).cuda()

            scores = model(word_batch)
            scores = scores.view(-1, granularity)
            labels = labels.view(-1)

            # get accuracy scores
            for idx, i in enumerate(scores):
                _, pos = i.max(0)
                if pos == labels[idx]:
                    running_acc += 1

            loss = criterion(scores, labels)
            running_loss += loss.item()
            iteration_count += 1

    test_time = time.time() - start
    accuracy = (running_acc / num_samples) * 100
    loss = running_loss / iteration_count

    return loss, accuracy, test_time


def main():
    # Collect input arguments & hyperparameters
    args = get_args()
    batch_size = args.batch_size
    learn_rate = args.lr
    epochs = args.epochs
    fine_grain = False
    layer = args.layer
    vis = Visualizations()

    if fine_grain is True:
        granularity = 5
    else:
        granularity = 2

    # set tokenizer to process dataset with
    tokenizer = 'moses'
    # Load SST datasets into memory
    print("Processing datasets..")
    train_data = SST(mode='train', subtrees=False, fine_grained=fine_grain,
                     tokenizer=tokenizer)
    val_data = SST(mode='val', subtrees=False, fine_grained=fine_grain,
                   tokenizer=tokenizer)
    test_data = SST(mode='test', subtrees=False, fine_grained=fine_grain,
                    tokenizer=tokenizer)

    # Printout dataset stats
    print(f"Training samples: {len(train_data)}")
    print(f"Validation samples: {len(val_data)}")
    print(f"Testing samples: {len(test_data)}")

    # Load datasets into torch dataloaders
    train_data = DataLoader(train_data, batch_size=batch_size, pin_memory=True,
                            shuffle=True, num_workers=6, collate_fn=GLoVeCollate())

    val_data = DataLoader(val_data, batch_size=batch_size, pin_memory=True,
                          shuffle=False, num_workers=6, collate_fn=GLoVeCollate())

    test_data = DataLoader(test_data, batch_size=batch_size, pin_memory=True,
                           shuffle=False, num_workers=6, collate_fn=GLoVeCollate())

    embedder = None

    # initialize probing model
    model = LinearSST(embedding_dim=300, granularity=granularity)
    model = model.cuda()

    # set loss function and optimizer
    criterion = nn.NLLLoss()
    # optimizer = optim.SGD(model.parameters(), lr=0.0005, momentum=0.9)
    optimizer = optim.Adam(model.parameters(), lr=learn_rate)

    best_acc = 0
    for epoch in range(epochs):
        loss, acc, time = train(train_data, model, criterion, optimizer, embedder,
                                       layer, granularity)

        val_loss, val_acc, val_time = test(val_data, model, criterion, embedder,
                                              layer, granularity)
        test_loss, test_acc, test_time = test(test_data, model, criterion, embedder,
                                              layer, granularity)

        if test_acc > best_acc:
            best_acc = test_acc
            # print("Best")
            # printout epoch stats
            print_loss(epoch, 'train', loss, acc, time)
            print_loss(epoch, 'val  ', val_loss, val_acc, val_time)
            print_loss(epoch, 'test ', test_loss, test_acc, test_time)
            print("")

        # plot epoch stats
        vis.plot_loss(loss, epoch, 'train')
        vis.plot_loss(val_loss, epoch, 'val')
        vis.plot_loss(test_loss, epoch, 'test')



if __name__ == '__main__':
    main()
