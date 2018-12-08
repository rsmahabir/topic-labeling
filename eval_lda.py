import argparse
import gc
from os.path import join, exists
from time import time

import numpy as np
import pandas as pd
from gensim.models import CoherenceModel

from constants import PARAMS, NBTOPICS, DATASETS, LDA_PATH
from utils import init_logging, load, log_args, TopicsLoader
import warnings
warnings.simplefilter(action='ignore', category=FutureWarning)


def cosine_similarities(vector_1, vectors_all):
    """Compute cosine similarities between one vector and a set of other vectors.

    Parameters
    ----------
    vector_1 : numpy.ndarray
        Vector from which similarities are to be computed, expected shape (dim,).
    vectors_all : numpy.ndarray
        For each row in vectors_all, distance from vector_1 is computed, expected shape (num_vectors, dim).

    Returns
    -------
    numpy.ndarray
        Contains cosine distance between `vector_1` and each row in `vectors_all`, shape (num_vectors,).

    """
    norm = np.linalg.norm(vector_1)
    all_norms = np.linalg.norm(vectors_all, axis=1)
    dot_products = np.dot(vectors_all, vector_1)
    similarities = dot_products / (norm * all_norms)
    return similarities


def pairwise_similarity(topic, kvs, ignore_oov=True):
    similarities = dict()
    for name, kv in kvs.items():
        vector = lambda x: kv[x] if x in kv else np.nan
        vectors = topic.map(vector).dropna()
        vectors = vectors.apply(pd.Series).values
        sims = np.asarray([cosine_similarities(vec, vectors) for vec in vectors]).mean(axis=0)
        if not ignore_oov:
            missing = len(topic) - len(sims)
            if missing > 0:
                sims = np.append(sims, np.zeros(missing))
        similarity = sims.mean()
        similarities[name] = similarity
    return pd.Series(similarities)


def mean_similarity(topic, kvs):
    similarities = dict()
    for name, kv in kvs.items():
        vector = lambda x: kv[x] if x in kv else np.nan
        vectors = topic.map(vector).dropna()
        vectors = vectors.apply(pd.Series).values
        mean_vec = np.mean(vectors, axis=0)
        similarity = cosine_similarities(mean_vec, vectors).mean()
        similarities[name] = similarity
    return pd.Series(similarities)


def eval_coherence(
        topics, dictionary, corpus=None, texts=None, keyed_vectors=None, metrics=None, window_size=None,
        suffix='', cores=1, logg=print
):
    if not (corpus or texts or keyed_vectors):
        logg('provide corpus, texts and/or keyed_vectors')
        return
    if metrics is None:
        if corpus is not None:
            metrics = ['u_mass']
        if texts is not None:
            if metrics is None:
                metrics = ['c_v', 'c_npmi', 'c_uci']
            else:
                metrics += ['c_v', 'c_npmi', 'c_uci']
        if keyed_vectors is not None:
            if metrics is None:
                metrics = ['c_w2v']
            else:
                metrics += ['c_w2v']

    # add out of vocabulariy terms dictionary and documents
    in_dict = topics.applymap(lambda x: x in dictionary.token2id)
    oov = topics[~in_dict]
    oov = oov.apply(set)
    oov = set().union(*oov)
    isstr = lambda x: isinstance(x, str)
    tolist = lambda x: [x]
    oov = sorted(map(tolist, filter(isstr, oov)))
    logg(f'OOV: {oov}')
    if oov:
        dictionary.add_documents(oov, prune_at=None)
        _ = dictionary[0]

    scores = dict()
    topics_values = topics.values
    for metric in metrics:
        t0 = time()
        gc.collect()
        logg(metric)
        txt = texts + oov if texts else None
        cm = CoherenceModel(
            topics=topics_values,
            dictionary=dictionary,
            corpus=corpus,
            texts=txt,
            coherence=metric,
            topn=10,
            window_size=window_size,
            processes=cores,
            keyed_vectors=keyed_vectors
        )
        coherence_scores = cm.get_coherence_per_topic(with_std=True, with_support=True)
        scores[metric + suffix] = coherence_scores
        gc.collect()
        t1 = int(time() - t0)
        logg("    done in {:02d}:{:02d}:{:02d}".format(t1 // 3600, (t1 // 60) % 60, t1 % 60))

    df = pd.DataFrame(scores)
    df.index = topics.index
    gc.collect()
    return df


def parse_args():
    parser = argparse.ArgumentParser()

    parser.add_argument("--dataset", type=str, required=True)
    parser.add_argument("--version", type=str, required=False, default='noun')
    parser.add_argument('--tfidf', dest='tfidf', action='store_true', required=False)
    parser.add_argument('--no-tfidf', dest='tfidf', action='store_false', required=False)
    parser.set_defaults(tfidf=False)
    parser.add_argument("--params", nargs='*', type=str, required=False, default=PARAMS)
    parser.add_argument("--nbtopics", nargs='*', type=int, required=False, default=NBTOPICS)
    parser.add_argument("--topn", type=int, required=False, default=10)
    parser.add_argument("--cores", type=int, required=False, default=4)
    parser.add_argument("--method", type=str, required=False, default='both',
                        choices=['coherence', 'w2v', 'both'])

    args = parser.parse_args()

    args.dataset = DATASETS.get(args.dataset, args.dataset)
    corpus_type = "tfidf" if args.tfidf else "bow"
    use_coherence = (args.method in ['coherence', 'both'])
    use_w2v = (args.method in ['w2v', 'both'])

    return (
        args.dataset, args.version, args.params, args.nbtopics, args.topn, args.cores, corpus_type,
        use_coherence, use_w2v, args
    )


def main():
    (
        dataset, version, params, nbtopics, topn, cores, corpus_type,
        use_coherence, use_w2v, args
    ) = parse_args()

    logger = init_logging(name=f'Eval_lda_{dataset}', basic=False, to_stdout=True, to_file=True)
    log_args(logger, args)

    tl = TopicsLoader(
        dataset=dataset,
        param_ids=params,
        nbs_topics=nbtopics,
        version=version,
        topn=topn,
        include_corpus=use_coherence,
        include_texts=use_coherence,
        logger=logger
    )
    topics = tl.topics
    logger.info(f'number of topics: {len(topics)}')
    wiki_dict = load('dict', 'dewiki', 'unfiltered', logger=logger)

    dfs = []
    if use_coherence:
        df = eval_coherence(
            topics=topics, dictionary=tl.dictionary, corpus=tl.corpus, texts=tl.texts,
            keyed_vectors=None, metrics=None, window_size=None,
            suffix='', cores=cores, logg=logger.info,
        )
        gc.collect()
        dfs.append(df)

        wiki_texts = load('texts', 'dewiki', logger=logger)
        df = eval_coherence(
            topics=topics, dictionary=wiki_dict, corpus=None, texts=wiki_texts,
            keyed_vectors=None, metrics=None, window_size=None,
            suffix='_wikt', cores=cores, logg=logger.info,
        )
        gc.collect()
        dfs.append(df)

        df = eval_coherence(
            topics, wiki_dict, corpus=None, texts=wiki_texts,
            keyed_vectors=None, metrics=['c_uci'], window_size=20,
            suffix='_wikt_w20', cores=cores, logg=logger.info,
        )
        del wiki_texts
        gc.collect()
        dfs.append(df)

    df_sims = None
    if use_w2v:
        d2v = load('d2v').docvecs
        w2v = load('w2v').wv
        ftx = load('ftx').wv
        # Dry run to make sure both indices are fully in RAM
        d2v.init_sims()
        _ = d2v.vectors_docs_norm[0]
        w2v.init_sims()
        _ = w2v.vectors_norm[0]
        ftx.init_sims()
        _ = ftx.vectors_norm[0]

        df = eval_coherence(
            topics=topics, dictionary=wiki_dict, corpus=None, texts=None,
            keyed_vectors=w2v, metrics=None, window_size=None,
            suffix='_w2v', cores=cores, logg=logger.info,
        )
        gc.collect()
        dfs.append(df)

        df = eval_coherence(
            topics=topics, dictionary=wiki_dict, corpus=None, texts=None,
            keyed_vectors=ftx, metrics=None, window_size=None,
            suffix='_ftx', cores=cores, logg=logger.info,
        )
        gc.collect()
        dfs.append(df)

        # apply custom similarity metrics
        kvs = {'d2v': d2v, 'w2v': w2v, 'ftx': ftx}
        ms = topics.apply(lambda x: mean_similarity(x, kvs), axis=1)
        ps = topics.apply(lambda x: pairwise_similarity(x, kvs, ignore_oov=True), axis=1)
        ps2 = topics.apply(lambda x: pairwise_similarity(x, kvs, ignore_oov=False), axis=1)
        df_sims = pd.concat(
            {'mean_similarity': ms, 'pairwise_similarity_ignore_oov': ps, 'pairwise_similarity': ps2},
            axis=1
        )
        del d2v, w2v, ftx
        gc.collect()

    dfs = pd.concat(dfs, axis=1)
    dfs = dfs.stack().apply(pd.Series).rename(columns={0: 'score', 1: 'stdev', 2: 'support'}).unstack()
    if df_sims is not None:
        dfs = pd.concat([dfs, df_sims], axis=1)

    file = join(
        LDA_PATH, version, corpus_type, 'topics', f'{dataset}_{version}_{corpus_type}_topic-scores.csv'
    )
    if exists(file):
        file = file.rstrip('.csv') + '_' + str(time()).split('.')[0] + '.csv'
    logger.info(f'Writing {file}')
    dfs.to_csv(file)
    return dfs


if __name__ == '__main__':
    main()