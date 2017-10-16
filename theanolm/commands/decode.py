#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""A module that implements the "theanolm decode" command.
"""

import sys
import os
import logging

import numpy
import theano

from theanolm import Network
from theanolm.backend import TextFileType
from theanolm.scoring import LatticeDecoder, SLFLattice

def add_arguments(parser):
    """Specifies the command line arguments supported by the "theanolm decode"
    command.

    :type parser: argparse.ArgumentParser
    :param parser: a command line argument parser
    """

    argument_group = parser.add_argument_group("files")
    argument_group.add_argument(
        'model_path', metavar='MODEL-FILE', type=str,
        help='the model file that will be used to decode the lattice')
    argument_group.add_argument(
        '--lattices', metavar='FILE', type=str, nargs='*', default=[],
        help='word lattices to be decoded (SLF, assumed to be compressed if '
             'the name ends in ".gz")')
    argument_group.add_argument(
        '--lattice-list', metavar='FILE', type=TextFileType('r'),
        help='text file containing a list of word lattices to be decoded (one '
             'path to an SLF file per line, the list and the SLF files are '
             'assumed to be compressed if the name ends in ".gz")')
    argument_group.add_argument(
        '--output-file', metavar='FILE', type=TextFileType('w'), default='-',
        help='where to write the best paths through the lattices (default '
             'stdout, will be compressed if the name ends in ".gz")')
    argument_group.add_argument(
        '--num-jobs', metavar='J', type=int, default=1,
        help='divide the set of lattice files into J distinct batches, and '
             'process only batch I')
    argument_group.add_argument(
        '--job', metavar='I', type=int, default=0,
        help='the index of the batch that this job should process, between 0 '
             'and J-1')

    argument_group = parser.add_argument_group("decoding")
    argument_group.add_argument(
        '--output', metavar='FORMAT', type=str, default='ref',
        help='format of the output, one of "ref" (default, utterance ID '
             'followed by words), "trn" (words followed by utterance ID in '
             'parentheses), "full" (utterance ID, acoustic score, language '
             'score, and number of words, followed by words)')
    argument_group.add_argument(
        '--n-best', metavar='N', type=int, default=1,
        help='print N best paths of each lattice (default 1)')
    argument_group.add_argument(
        '--nnlm-weight', metavar='LAMBDA', type=float, default=1.0,
        help="language model probabilities given by the model read from "
             "MODEL-FILE will be weighted by LAMBDA, when interpolating with "
             "the language model probabilities in the lattice (default is 1.0, "
             "meaning that the LM probabilities in the lattice will be "
             "ignored)")
    argument_group.add_argument(
        '--lm-scale', metavar='LMSCALE', type=float, default=None,
        help="scale language model log probabilities by LMSCALE when computing "
             "the total probability of a path (default is to use the LM scale "
             "specified in the lattice file, or 1.0 if not specified)")
    argument_group.add_argument(
        '--wi-penalty', metavar='WIP', type=float, default=None,
        help="penalize word insertion by adding WIP to the total log "
             "probability as many times as there are words in the path "
             "(without scaling WIP by LMSCALE)")
    argument_group.add_argument(
        '--log-base', metavar='B', type=int, default=None,
        help="convert output log probabilities to base B and WIP from base B "
             "(default is natural logarithm; this does not affect reading "
             "lattices, since they specify their internal log base)")
    argument_group.add_argument(
        '--unk-penalty', metavar='LOGPROB', type=float, default=None,
        help="use constant LOGPROB as <unk> token score (default is to use the "
             "network to predict <unk> probability)")
    argument_group.add_argument(
        '--shortlist', action="store_true",
        help='distribute <unk> token probability among the out-of-shortlist '
             'words according to their unigram frequencies in the training '
             'data')
    argument_group.add_argument(
        '--unk-from-lattice', action="store_true",
        help='use only the probability from the lattice for <unk> tokens')
    argument_group.add_argument(
        '--linear-interpolation', action="store_true",
        help="use linear interpolation of language model probabilities, "
             "instead of (pseudo) log-linear")

    argument_group = parser.add_argument_group("pruning")
    argument_group.add_argument(
        '--max-tokens-per-node', metavar='T', type=int, default=None,
        help="keep only at most T tokens at each node when decoding a lattice "
             "(default is no limit)")
    argument_group.add_argument(
        '--beam', metavar='B', type=float, default=None,
        help="prune tokens whose log probability is at least B smaller than "
             "the log probability of the best token at any given time (default "
             "is no beam pruning)")
    argument_group.add_argument(
        '--recombination-order', metavar='O', type=int, default=None,
        help="keep only the best token, when at least O previous words are "
             "identical (default is to recombine tokens only if the entire "
             "word history matches)")

    argument_group = parser.add_argument_group("logging and debugging")
    argument_group.add_argument(
        '--log-file', metavar='FILE', type=str, default='-',
        help='path where to write log file (default is standard output)')
    argument_group.add_argument(
        '--log-level', metavar='LEVEL', type=str, default='info',
        help='minimum level of events to log, one of "debug", "info", "warn" '
             '(default "info")')
    argument_group.add_argument(
        '--debug', action="store_true",
        help='enables debugging Theano errors')
    argument_group.add_argument(
        '--profile', action="store_true",
        help='enables profiling Theano functions')

def decode(args):
    """A function that performs the "theanolm decode" command.

    :type args: argparse.Namespace
    :param args: a collection of command line arguments
    """

    log_file = args.log_file
    log_level = getattr(logging, args.log_level.upper(), None)
    if not isinstance(log_level, int):
        print("Invalid logging level requested:", args.log_level)
        sys.exit(1)
    log_format = '%(asctime)s %(funcName)s: %(message)s'
    if args.log_file == '-':
        logging.basicConfig(stream=sys.stdout, format=log_format, level=log_level)
    else:
        logging.basicConfig(filename=log_file, format=log_format, level=log_level)

    if args.debug:
        theano.config.compute_test_value = 'warn'
    else:
        theano.config.compute_test_value = 'off'
    theano.config.profile = args.profile
    theano.config.profile_memory = args.profile

    network = Network.from_file(args.model_path,
                                mode=Network.Mode(minibatch=False))

    log_scale = 1.0 if args.log_base is None else numpy.log(args.log_base)

    if args.wi_penalty is None:
        wi_penalty = None
    else:
        wi_penalty = args.wi_penalty * log_scale
    decoding_options = {
        'nnlm_weight': args.nnlm_weight,
        'lm_scale': args.lm_scale,
        'wi_penalty': wi_penalty,
        'unk_penalty': args.unk_penalty,
        'use_shortlist': args.shortlist,
        'unk_from_lattice': args.unk_from_lattice,
        'linear_interpolation': args.linear_interpolation,
        'max_tokens_per_node': args.max_tokens_per_node,
        'beam': args.beam,
        'recombination_order': args.recombination_order
    }
    logging.debug("DECODING OPTIONS")
    for option_name, option_value in decoding_options.items():
        logging.debug("%s: %s", option_name, str(option_value))

    print("Building word lattice decoder.")
    sys.stdout.flush()
    decoder = LatticeDecoder(network, decoding_options)

    # Combine paths from command line and lattice list.
    lattices = args.lattices
    if args.lattice_list is not None:
        lattices.extend(args.lattice_list.readlines())
    lattices = [path.strip() for path in lattices]
    # Ignore empty lines in the lattice list.
    lattices = [x for x in lattices if x]
    # Pick every Ith lattice, if --num-jobs is specified and > 1.
    if args.num_jobs < 1:
        print("Invalid number of jobs specified:", args.num_jobs)
        sys.exit(1)
    if (args.job < 0) or (args.job > args.num_jobs - 1):
        print("Invalid job specified:", args.job)
        sys.exit(1)
    lattices = lattices[args.job::args.num_jobs]

    file_type = TextFileType('r')
    for index, path in enumerate(lattices):
        logging.info("Reading word lattice: %s", path)
        lattice_file = file_type(path)
        lattice = SLFLattice(lattice_file)

        if lattice.utterance_id is not None:
            utterance_id = lattice.utterance_id
        else:
            utterance_id = os.path.basename(lattice_file.name)
        logging.info("Utterance `%s' -- %d/%d of job %d",
                     utterance_id,
                     index + 1,
                     len(lattices),
                     args.job)
        tokens = decoder.decode(lattice)

        for index in range(min(args.n_best, len(tokens))):
            line = format_token(tokens[index],
                                utterance_id,
                                network.vocabulary,
                                log_scale,
                                args.output)
            args.output_file.write(line + "\n")

def format_token(token, utterance_id, vocabulary, log_scale, output_format):
    """Formats an output line from a token and an utterance ID.

    Reads word IDs from the history list of ``token`` and converts them to words
    using ``vocabulary``. The history may contain also OOV words as text, so any
    ``str`` will be printed literally.

    :type token: Token
    :param token: a token whose history will be formatted

    :type utterance_id: str
    :param utterance_id: utterance ID for full output

    :type vocabulary: Vocabulary
    :param vocabulary: mapping from word IDs to words

    :type log_scale: float
    :param log_scale: divide log probabilities by this number to convert the log
                      base

    :type output_format: str
    :param output_format: which format to write, one of "ref" (utterance ID,
        words), "trn" (words, utterance ID in parentheses), "full" (utterance
        ID, acoustic and LM scores, number of words, words)

    :rtype: str
    :returns: the formatted output line
    """

    words = token.history_words(vocabulary)
    if output_format == 'ref':
        return "{} {}".format(utterance_id, ' '.join(words))
    elif output_format == 'trn':
        return "{} ({})".format(' '.join(words), utterance_id)
    elif output_format == 'full':
        return "{} {} {} {} {}".format(
            utterance_id,
            token.ac_logprob / log_scale,
            token.lm_logprob / log_scale,
            len(words),
            ' '.join(words))
    else:
        print("Invalid output format requested:", args.output)
        sys.exit(1)
