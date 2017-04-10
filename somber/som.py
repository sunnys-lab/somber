import logging
import time
import numpy as np

from somber.utils import progressbar, expo, linear, static
from functools import reduce
from collections import defaultdict, Counter, OrderedDict
from sklearn.decomposition.pca import PCA


logger = logging.getLogger(__name__)


class Som(object):
    """
    This is the basic SOM class.
    """

    def __init__(self,
                 map_dim,
                 dim,
                 learning_rate,
                 lrfunc=expo,
                 nbfunc=expo,
                 sigma=None,
                 min_max=np.argmin):
        """

        :param map_dim: A tuple of map dimensions, e.g. (10, 10) instantiates a 10 by 10 map.
        :param dim: The data dimensionality.
        :param learning_rate: The learning rate, which is decreases according to some function
        :param lrfunc: The function to use in decreasing the learning rate. The functions are
        defined in utils. Default is exponential.
        :param nbfunc: The function to use in decreasing the neighborhood size. The functions
        are defined in utils. Default is exponential.
        :param sigma: The starting value for the neighborhood size, which is decreased over time.
        If sigma is None (default), sigma is calculated as ((max(map_dim) / 2) + 0.01), which is
        generally a good value.
        """

        if sigma is not None:
            self.sigma = sigma
        else:
            # Add small constant to sigma to prevent divide by zero for maps of size 2.
            self.sigma = (max(map_dim) / 2.0) + 0.01

        self.learning_rate = learning_rate
        # A tuple of dimensions
        # Usually (width, height), but can accomodate one-dimensional maps.
        self.map_dimensions = map_dim

        # The dimensionality of the weight vector
        # Usually (width * height)
        self.map_dim = reduce(np.multiply, map_dim, 1)

        # Weights are initialized to small random values.
        # Initializing to more appropriate values given the dataset
        # will probably give faster convergence.
        self.weights = np.zeros((self.map_dim, dim))
        self.data_dim = dim

        # The function used to diminish the learning rate.
        self.lrfunc = lrfunc
        # The function used to diminish the neighborhood size.
        self.nbfunc = nbfunc

        # Initialize the distance grid: only needs to be done once.
        self.distance_grid = self._initialize_distance_grid()
        self.min_max = min_max
        self.trained = False

    def train(self, X, num_epochs, total_updates=1000, stop_lr_updates=1.0, stop_nb_updates=1.0, context_mask=(), show_progressbar=False):
        """
        Fits the SOM to some data.
        The updates correspond to the number of updates to the parameters
        (i.e. learning rate, neighborhood, not weights!) to perform during training.

        In general, 1000 updates will do for most learning problems.

        :param X: the data on which to train.
        :param total_updates: The number of updates to the parameters to do during training.
        :param stop_lr_updates: A fraction, describing over which portion of the training data
        the neighborhood and learning rate should decrease. If the total number of updates, for example
        is 1000, and stop_updates = 0.5, 1000 updates will have occurred after half of the examples.
        After this period, no updates of the parameters will occur.
        :param context_mask: a binary mask used to indicate whether the context should be set to 0
        at that specified point in time. Used to make items conditionally independent on previous items.
        Examples: Spaces in character-based models of language. Periods and question marks in models of sentences.
        :return: None
        """

        if not self.trained:
            min_ = np.min(X, axis=0)
            random = np.random.rand(self.map_dim).reshape((self.map_dim, 1))
            temp = np.outer(random, np.abs(np.max(X, axis=0) - min_))
            self.weights = min_ + temp

        train_length = len(X) * num_epochs

        # The step size is the number of items between rough epochs.
        # We use len instead of shape because len also works with np.flatiter
        step_size_lr = max((train_length * stop_lr_updates) // total_updates, 1)
        step_size_nb = max((train_length * stop_nb_updates) // total_updates, 1)

        if step_size_lr == 1 or step_size_nb == 1:
            logger.warning("step size of learning rate or neighborhood is 1")

        logger.info("{0} items, {1} epochs, total length: {2}".format(len(X), num_epochs, train_length))

        # Precalculate the number of updates.
        lr_update_counter = np.arange(step_size_lr, (train_length * stop_lr_updates) + step_size_lr, step_size_lr)
        nb_update_counter = np.arange(step_size_nb, (train_length * stop_nb_updates) + step_size_nb, step_size_nb)
        start = time.time()

        # Train
        self._train_loop(X, num_epochs, lr_update_counter, nb_update_counter, context_mask, show_progressbar)
        self.trained = True

        logger.info("Total train time: {0}".format(time.time() - start))

    def _init_pca(self, X):

        coordinates = []
        coordinates.append(np.arange(self.map_dim) // self.map_dimensions[1])
        coordinates.append(np.arange(self.map_dim) % self.map_dimensions[1])
        coordinates = np.array(coordinates).T

        data = (X - np.mean(X, axis=0))
        tmp_matrix = np.tile(np.mean(X, axis=0), (self.map_dim, 1))

        pca = PCA(n_components=2, svd_solver='randomized')
        pca.fit(data)
        components = (pca.components_.T / np.sqrt(np.sum(np.square(pca.components_), axis=1)))
        components *= pca.explained_variance_
        components = components.T

        for j in range(self.map_dim):
            tmp_matrix[j] += coordinates[j, :] * components

        return tmp_matrix

    def _train_loop(self, X, num_epochs, lr_update_counter, nb_update_counter, context_mask, show_progressbar):
        """
        The train loop. Is a separate function to accomodate easy inheritance.

        :param X: The input data.
        :param lr_update_counter: A list of indices at which the params need to be updated.
        :param context_mask: Not used in the standard SOM.
        :return: None
        """

        nb_step = 0
        lr_step = 0

        # Calculate the influences for update 0.
        map_radius = self.nbfunc(self.sigma, 0, len(nb_update_counter))
        learning_rate = self.lrfunc(self.learning_rate, 0, len(lr_update_counter))
        influences = self._calculate_influence(map_radius) * learning_rate
        update = False

        idx = 0

        for epoch in range(num_epochs):

            for x in progressbar(X, use=show_progressbar):

                self._example(x, influences)

                if idx in nb_update_counter:
                    nb_step += 1

                    map_radius = self.nbfunc(self.sigma, nb_step, len(nb_update_counter))
                    update = True

                if idx in lr_update_counter:
                    lr_step += 1

                    learning_rate = self.lrfunc(self.learning_rate, lr_step, len(lr_update_counter))
                    update = True

                if update:

                    influences = self._calculate_influence(map_radius) * learning_rate
                    update = False

                idx += 1

    def _example(self, x, influences, **kwargs):
        """
        A single example.

        :param x: a single example
        :param influences: an array with influence values.
        :return: The best matching unit
        """

        activation, difference_x = self._get_bmus(x)

        influences, bmu = self._apply_influences(activation, influences)
        self.weights += self._calculate_update(difference_x, influences)

        return activation

    def _calculate_update(self, difference_vector, influence):
        """
        Updates the nodes, conditioned on the input vector,
        the influence, as calculated above, and the learning rate.

        Uses Oja's Rule: delta_W = alpha * (X - w)

        In this case (X - w) has been precomputed for speed, in the function
        _get_bmus.

        :param difference_vector: The difference between the input and some weights.
        :param influence: The influence the result has on each unit, depending on distance.
        Already includes the learning rate.
        """

        return difference_vector * influence

    def _get_bmus(self, x):
        """
        Gets the best matching units, based on euclidean distance.

        :param x: The input vector
        :return: The activations, which is a vector of map_dim, and
         the distances between the input and the weights, which can be
         reused in the update calculation.
        """

        differences = self._distance_difference(x, self.weights)

        # squared euclidean distance.
        activations = np.sum(np.square(differences), axis=1)
        return activations, differences

    def _distance_difference(self, x, weights):
        """
        Calculates the difference between an input and all the weights.

        :param x: The input.
        :param weights: An array of weights.
        :return: A vector of differences.
        """

        return x - weights

    def _predict_base(self, X):
        """
        Predicts distances to some input data.

        :param X: The input data.
        :return: An array of arrays, representing the activation
        each node has to each input.
        """

        distances = []

        for x in X:
            distance, _ = self._get_bmus(x)
            distances.append(distance)

        return distances

    def quant_error(self, X):
        """
        Calculates the quantization error by taking the minimum euclidean
        distance between the units and some input.

        :param X: Input data.
        :return: A vector of numbers, representing the quantization error
        for each data point.
        """

        dist = self._predict_base(X)
        return np.min(dist, axis=1)

    def predict(self, X):
        """
        Predict the BMU for each input data.

        :param X: Input data.
        :return: The index of the bmu which best describes the input data.
        """

        dist = self._predict_base(X)
        return self.min_max(dist, axis=1)

    def receptive_field(self, X, identities, max_len=5, threshold=0.9):
        """
        Calculate the receptive field of the SOM on some data.

        The receptive field is the common ending of all sequences which
        lead to the activation of a given BMU. If a SOM is well-tuned to
        specific sequences, it will have longer receptive fields, and therefore
        gives a better description of the dynamics of a given system.

        :param X: Input data.
        :param identities: The letters associated with each input datum.
        :param max_len: The maximum length sequence we expect.
        Increasing the window size leads to accurate results,
        but costs more memory.
        :return: The receptive field of each neuron.
        """

        assert len(X) == len(identities)

        receptive_fields = defaultdict(list)
        predictions = self.predict(X)

        for idx, p in enumerate(predictions):

            receptive_fields[p].append(identities[idx+1 - max_len:idx+1])

        sequence = defaultdict(list)

        for k, v in receptive_fields.items():

            v = [x for x in v if x]

            total = len(v)
            v = ["".join([str(x_) for x_ in x]) for x in v]

            for row in reversed(list(zip(*v))):

                r = Counter(row)

                for _, count in r.items():
                    if count / total > threshold:
                        sequence[k].append(row[0])
                    else:
                        break

        return ({k: v[::-1] for k, v in sequence.items()},
                {k: Counter(["".join(x) for x in v]) for k, v in receptive_fields.items()})

    def invert_projection(self, X, identities):
        """
        Calculate the inverted projection of a SOM by associating each
        unit with the input datum that gives the closest match.

        Works best for symbolic (instead of continuous) input data.

        :param X: Input data
        :param identities: The identities for each
        input datum, must be same length as X
        :return: A numpy array with identities, the shape of the map.
        """

        assert len(X) == len(identities)

        # Remove all duplicates from X
        X_unique, names = zip(*set([tuple((tuple(s), n)) for s, n in zip(X, identities)]))
        node_match = []

        for node in list(self.weights):

            differences = self._distance_difference(node, X_unique)
            distances = np.sum(np.square(differences), axis=1)
            node_match.append(names[np.argmin(distances)])

        return np.array(node_match)

    def _apply_influences(self, activations, influences):
        """
        First calculates the BMU.
        Then gets the appropriate influence from the neighborhood, given the BMU

        :param activations: A Numpy array of distances.
        :param influences: A (map_dim, map_dim, data_dim) array describing the influence
        each node has on each other node.
        :return: The influence given the bmu, and the index of the bmu itself.
        """

        bmu = self.min_max(activations)
        return influences[bmu], bmu

    def _calculate_influence(self, sigma):
        """
        Pre-calculates the influence for a given value of sigma.

        The neighborhood has size map_dim * map_dim, so for a 30 * 30 map, the neighborhood will be
        size (900, 900). It is then duplicated _data_dim times, and reshaped into an
        (map_dim, map_dim, data_dim) array. This is done to facilitate fast calculation in subsequent steps.

        :param sigma: The neighborhood value.
        :return: The neighborhood, reshaped into an array
        """

        neighborhood = np.exp(-self.distance_grid / (2.0 * sigma ** 2)).reshape(self.map_dim, self.map_dim)
        return np.asarray([neighborhood] * self.data_dim).transpose((1, 2, 0))

    def _initialize_distance_grid(self):
        """
        Initializes the distance grid by calls to _grid_dist.

        :return:
        """

        return np.array([self._grid_distance(i).reshape(1, self.map_dim) for i in range(self.map_dim)])

    def _grid_distance(self, index):
        """
        Calculates the distance grid for a single index position. This is pre-calculated for
        fast neighborhood calculations later on (see _calc_influence).

        :param index: The index for which to calculate the distances.
        :return: A flattened version of the distance array.
        """

        width, height = self.map_dimensions

        row = index // width
        column = index % width

        r = np.arange(height)[:, np.newaxis]
        c = np.arange(width)
        distance = (r-row)**2 + (c-column)**2

        return distance.ravel()

    def map_weights(self):
        """
        Retrieves the grid as a list of lists of weights. For easy visualization.

        :return: A three-dimensional Numpy array of values (width, height, data_dim)
        """

        width, height = self.map_dimensions

        return self.weights.reshape((width, height, self.data_dim)).transpose(1, 0, 2)