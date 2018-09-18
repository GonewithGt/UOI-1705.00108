import os
import codecs
import numpy
import keras
from keras_bi_lm import BiLM
from keras_wc_embd import get_dicts_generator, get_batch_input, get_embedding_weights_from_file
from model import build_model


MODEL_PATH = 'model.h5'
MODEL_LM_PATH = 'model_lm.h5'

DATA_ROOT = 'dataset/CoNNL2003eng'
DATA_TRAIN_PATH = os.path.join(DATA_ROOT, 'train.txt')
DATA_VALID_PATH = os.path.join(DATA_ROOT, 'valid.txt')
DATA_TEST_PATH = os.path.join(DATA_ROOT, 'test.txt')

WORD_EMBD_PATH = 'dataset/glove.6B.100d.txt'

BATCH_SIZE = 16
EPOCHS = 0

TAGS = {
    'O': 0,
    'B-PER': 1,
    'I-PER': 2,
    'B-LOC': 3,
    'I-LOC': 4,
    'B-ORG': 5,
    'I-ORG': 6,
    'B-MISC': 7,
    'I-MISC': 8,
}


def load_data(path):
    sentences, taggings = [], []
    with codecs.open(path, 'r', 'utf8') as reader:
        for line in reader:
            line = line.strip()
            if not line:
                if not sentences or len(sentences[-1]) > 0:
                    sentences.append([])
                    taggings.append([])
                continue
            parts = line.split()
            if parts[0] != '-DOCSTART-':
                sentences[-1].append(parts[0])
                taggings[-1].append(TAGS[parts[-1]])
    if not sentences[-1]:
        sentences.pop()
        taggings.pop()
    return sentences, taggings


print('Loading...')
train_sentences, train_taggings = load_data(DATA_TRAIN_PATH)
valid_sentences, valid_taggings = load_data(DATA_VALID_PATH)

dicts_generator = get_dicts_generator(
    word_min_freq=2,
    char_min_freq=2,
    word_ignore_case=True,
    char_ignore_case=False
)
for sentence in train_sentences:
    dicts_generator(sentence)
word_dict, char_dict, max_word_len = dicts_generator(return_dict=True)
word_dict['<EOS>'] = len(word_dict)

if os.path.exists(WORD_EMBD_PATH):
    print('Embedding...')
    word_embd_weights = [[0.0] * 100] + numpy.random.random((len(word_dict) - 1, 100)).tolist()
    with codecs.open(WORD_EMBD_PATH, 'r', 'utf8') as reader:
        for line_num, line in enumerate(reader):
            if (line_num + 1) % 1000 == 0:
                print('Load embedding... %d' % (line_num + 1), end='\r', flush=True)
            line = line.strip()
            if not line:
                continue
            parts = line.split()
            word = parts[0].lower()
            if word in word_dict:
                word_embd_weights[word_dict[word]] = parts[1:]
    word_embd_weights = numpy.asarray(word_embd_weights)
    print('Dict size: %d  Shape of weights: %s' % (len(word_dict), str(word_embd_weights.shape)))
else:
    word_embd_weights = None
    print('Dict size: %d' % len(word_dict))

train_steps = (len(train_sentences) + BATCH_SIZE - 1) // BATCH_SIZE
valid_steps = (len(valid_sentences) + BATCH_SIZE - 1) // BATCH_SIZE

if os.path.exists(MODEL_LM_PATH):
    bi_lm_model = BiLM(model_path=MODEL_LM_PATH)
else:
    bi_lm_model = BiLM(
        token_num=len(word_dict),
        rnn_units=100,
        embedding_weights=word_embd_weights,
        embedding_dim=100,
    )
    bi_lm_model.model.summary()

    def lm_batch_generator(sentences, steps):
        global word_dict, char_dict, max_word_len
        while True:
            for i in range(steps):
                batch_sentences = sentences[BATCH_SIZE * i:min(BATCH_SIZE * (i + 1), len(sentences))]
                inputs, outputs = BiLM.get_batch(
                    sentences=batch_sentences,
                    token_dict=word_dict,
                    ignore_case=True,
                    unk_index=word_dict['<UNK>'],
                    eos_index=word_dict['<EOS>'],
                )
                yield inputs, outputs

    print('Fitting Bi-LM...')
    bi_lm_model.model.fit_generator(
        generator=lm_batch_generator(sentences=train_sentences, steps=train_steps),
        steps_per_epoch=train_steps,
        epochs=EPOCHS,
        validation_data=lm_batch_generator(sentences=valid_sentences, steps=valid_steps),
        validation_steps=valid_steps,
        callbacks=[
            keras.callbacks.EarlyStopping(monitor='val_loss', patience=2),
            keras.callbacks.EarlyStopping(monitor='val_Bi-LM-Dense-Forward_sparse_categorical_accuracy', patience=2),
            keras.callbacks.EarlyStopping(monitor='val_Bi-LM-Dense-Backward_sparse_categorical_accuracy', patience=2),
        ],
        verbose=True,
    )
    bi_lm_model.save_model(MODEL_LM_PATH)


def lm_batch_generator(sentences, steps):
    global word_dict, char_dict, max_word_len
    while True:
        for i in range(steps):
            batch_sentences = sentences[BATCH_SIZE * i:min(BATCH_SIZE * (i + 1), len(sentences))]
            inputs, outputs = BiLM.get_batch(
                sentences=batch_sentences,
                token_dict=word_dict,
                ignore_case=True,
                unk_index=word_dict['<UNK>'],
                eos_index=word_dict['<EOS>'],
            )
            yield inputs, outputs


def batch_generator(sentences, taggings, steps, training=True):
    global word_dict, char_dict, max_word_len
    while True:
        for i in range(steps):
            batch_sentences = sentences[BATCH_SIZE * i:min(BATCH_SIZE * (i + 1), len(sentences))]
            batch_taggings = taggings[BATCH_SIZE * i:min(BATCH_SIZE * (i + 1), len(taggings))]
            word_input, char_input = get_batch_input(
                batch_sentences,
                max_word_len,
                word_dict,
                char_dict,
                word_ignore_case=True,
                char_ignore_case=False
            )
            if not training:
                yield [word_input, char_input], batch_taggings
                continue
                pass
            sentence_len = word_input.shape[1]
            for j in range(len(batch_taggings)):
                batch_taggings[j] = batch_taggings[j] + [0] * (sentence_len - len(batch_taggings[j]))
                batch_taggings[j] = [[tag] for tag in batch_taggings[j]]
            batch_taggings = numpy.asarray(batch_taggings)
            yield [word_input, char_input], batch_taggings
        if not training:
            break


model = build_model(word_dict_len=len(word_dict),
                    char_dict_len=len(char_dict),
                    max_word_len=max_word_len,
                    word_dim=100,
                    char_dim=80,
                    char_embd_dim=25,
                    output_dim=len(TAGS),
                    bi_lm_model=bi_lm_model,
                    word_embd_weights=word_embd_weights)
model.summary()


if os.path.exists(MODEL_PATH):
    model.load_weights(MODEL_PATH, by_name=True)

print('Fitting...')
model.fit_generator(
    generator=batch_generator(train_sentences, train_taggings, train_steps),
    steps_per_epoch=train_steps,
    epochs=EPOCHS,
    validation_data=batch_generator(valid_sentences, valid_taggings, valid_steps),
    validation_steps=valid_steps,
    callbacks=[
        keras.callbacks.EarlyStopping(monitor='val_loss', patience=2),
        keras.callbacks.EarlyStopping(monitor='val_acc', patience=2),
    ],
    verbose=True,
)

model.save_weights(MODEL_PATH)

test_sentences, test_taggings = load_data(DATA_TEST_PATH)
test_steps = (len(valid_sentences) + BATCH_SIZE - 1) // BATCH_SIZE

print('Predicting...')


def get_tags(tags):
    filtered = []
    for i in range(len(tags)):
        if tags[i] == 0:
            continue
        if tags[i] % 2 == 1:
            filtered.append({
                'begin': i,
                'end': i,
                'type': i,
            })
        elif i > 0 and tags[i - 1] == tags[i] - 1:
            filtered[-1]['end'] += 1
    return filtered


eps = 1e-6
total_pred, total_true, matched_num = 0, 0, 0.0
for inputs, batch_taggings in batch_generator(
        test_sentences,
        test_taggings,
        test_steps,
        training=False):
    predict = model.predict_on_batch(inputs)
    predict = numpy.argmax(predict, axis=2).tolist()
    for i, pred in enumerate(predict):
        pred = get_tags(pred[:len(batch_taggings[i])])
        true = get_tags(batch_taggings[i])
        total_pred += len(pred)
        total_true += len(true)
        matched_num += sum([1 for tag in pred if tag in true])
precision = (matched_num + eps) / (total_pred + eps)
recall = (matched_num + eps) / (total_true + eps)
f1 = 2 * precision * recall / (precision + recall)
print('P: %.4f  R: %.4f  F: %.4f' % (precision, recall, f1))
