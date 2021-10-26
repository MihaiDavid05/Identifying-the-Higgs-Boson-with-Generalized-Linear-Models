import argparse
import numpy as np
from utils.config import read_config
from utils.data import create_csv_submission, prepare_train_data, prepare_test_data, load_csv_data, \
    do_cross_validation, split_data_by_jet, remove_useless_columns, drop_correlated
from utils.algo import predict_labels, get_f1, get_precision_recall_accuracy
from utils.implementations import model
from utils.vizualization import plot_loss

CONFIGS_PATH = '../configs/'


def parse_arguments():
    parser = argparse.ArgumentParser()
    parser.add_argument('config_filename', type=str, help='Config name that you want to use during the run.')
    parser.add_argument('--test', action='store_true', help='Also test and create submission file')
    parser.add_argument('--see_hist', action='store_true', help='See features histogram panel')
    parser.add_argument('--see_loss', action='store_true', help='See training loss plot')
    parser.add_argument('--see_pca', action='store_true', help='See PCA with 2 components')
    parser.add_argument('--sub_models', type=str, default='0,1,2', help='Choose which sub-model to run. 0 - '
                                                                        'jet_zero, 1- jet_one, 2 - more than 1 jet')

    return parser.parse_args()


def train(c, args, y, x, x_name, model_key=''):
    """
    Pipeline for training.
    :param c: Configuration parameters.
    :param args: Command line arguments.
    :param y: Labels.
    :param x: Train data.
    :param x_name: Features names.
    :param model_key: Sub model name if dataset is split.
    :return: Training statistics, weights and validation metrics if available
    """
    # Prepare data for training
    x, y, stat = prepare_train_data(c, args, y, x, x_name, model_key)

    # Perform cross validation
    val_f1, val_acc = -1, -1
    if c['cross_val']:
        val_f1, _, val_acc, _ = do_cross_validation(x, y, c)
        print("Cross validation f1 score is {:.2f} % and accuracy is {:.2f} %".format(val_f1 * 100, val_acc * 100))

    # Find weights with one of the models
    w, tr_loss = model(y, x, c)

    # Plot training loss
    if args.see_loss:
        output_path = c['paths']["viz_path"] + 'loss_plot_' + args.config_filename + '_' + model_key
        plot_loss(range(c['max_iters']), np.ravel(tr_loss), output_path=output_path)

    # Get predictions
    p = predict_labels(w, x, c["reg_threshold"])

    # Get F1 score and accuracy for training
    f1_score = get_f1(p, y)
    _, _, acc = get_precision_recall_accuracy(p, y)
    print("Training F1 score is {:.2f} % and accuracy is {:.2f} % \n".format(f1_score * 100, acc * 100))

    return stat, w, val_f1, val_acc


def test(c, s1, s2, w, x, x_name, i, model_key=''):
    """
    Pipeline for testing.
    :param c: Configuration parameters.
    :param s1: Feature-wise training mean or max - min
    :param s2: Feature-wise training standard deviation or min
    :param w: Weights.
    :param x: Test data.
    :param x_name: Features names.
    :param i: Test sample indexes.
    :return: Test indexes and predictions
    :param model_key: String name if sub_model is used.
    """
    # Prepare data for testing
    x, i, _ = prepare_test_data(c, s1, s2, x, x_name, x_index=i, model_key=model_key)
    # Get predictions
    p = predict_labels(w, x, c["reg_threshold"])

    return i, p


if __name__ == '__main__':
    # Parse arguments and get configurable parameters
    cli_args = parse_arguments()
    config_path = CONFIGS_PATH + cli_args.config_filename + '.yaml'
    config = read_config(config_path)
    output_filename = config['output_path'] + cli_args.config_filename + '_submission'
    by_jet = cli_args.config_filename.split('_')[-1] == '3models'
    cli_args.sub_models = [int(i) for i in cli_args.sub_models.split(',')]

    # If there are 3 subsets, split by jet number, predict on each of them
    if by_jet:
        # Load data
        labels_tr, x_tr, _, x_name_tr = load_csv_data(config['train_data'])
        _, x_te, index_te, x_name_te = load_csv_data(config['test_data'])

        # Define lists for test indexes, predictions and metric
        idxs, preds, total_f1, total_acc = [], [], [], []

        # Split data according to jet number
        data_dict_tr = split_data_by_jet(x_tr, labels_tr, np.zeros(x_tr.shape[0]))
        data_dict_te = split_data_by_jet(x_te, np.zeros(x_te.shape[0]), index_te)

        # Remove columns full of useless values
        data_dict_tr = remove_useless_columns(data_dict_tr, x_name_tr)
        data_dict_te = remove_useless_columns(data_dict_te, x_name_te)

        # Iterate through each subset
        for i, k in enumerate(data_dict_tr.keys()):
            if i in cli_args.sub_models:
                # Drop correlated features
                if config[k]["drop_corr"]:
                    data_dict_tr, tr_corr_idxs = drop_correlated(data_dict_tr, k, config)
                    data_dict_te, _ = drop_correlated(data_dict_te, k, config, tr_corr_idxs)

                # Get test indices, training and testing data and labels for a subset
                _, x_tr, labels_tr, x_name_tr = data_dict_tr[k]
                indices_te, x_te, _, x_name_te = data_dict_te[k]

                # Training and testing pipelines for a subset
                stats_tr, w_tr, te_f1, te_acc = train(config[k], cli_args, labels_tr, x_tr, x_name_tr, model_key=k)
                ind, pred = test(config[k], stats_tr[0], stats_tr[1], w_tr, x_te, x_name_te, indices_te, model_key=k)

                # Gather test indices, predictions and metrics
                preds.extend(list(np.ravel(pred)))
                idxs.extend(list(np.ravel(ind)))
                total_acc.append(te_acc)
                total_f1.append(te_f1)

        # Check that all sub-models were run
        if cli_args.sub_models != [0, 1, 2]:
            raise Exception("Not all sub models were run. Prediction file cannot be run. Check cli arguments!")

        # Print overall metrics for validation sets
        print("Overall validation F1 score is {:.2f} % and accuracy is {:.2f} %".format(np.mean(total_f1) * 100,
                                                                                        np.mean(total_acc) * 100))

        # Sort predictions by index and create submission
        idxs, preds = zip(*sorted(zip(idxs, preds), key=lambda x: x[0]))
        create_csv_submission(idxs, preds, output_filename)
    else:
        # Load data
        labels_tr, x_tr, _, x_name_tr = load_csv_data(config['train_data'])
        _, x_te, index_te, x_name_te = load_csv_data(config['test_data'])

        # Train pipeline
        stats, w_tr, _, _ = train(config, cli_args, labels_tr, x_tr, x_name_tr)

        # Test pipeline and create submission
        ind, pred = test(config, stats[0], stats[1], w_tr, x_te, x_name_te, index_te)
        create_csv_submission(ind, pred, output_filename)

    # TODO (maybe): We have an unbalanced dataset: 85667 signals, 164333 backgrounds, try class weighted reg
    # https://machinelearningmastery.com/cost-sensitive-logistic-regression/