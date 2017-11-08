from __future__ import division, print_function, absolute_import
import numpy as np
from sklearn.metrics import average_precision_score, roc_auc_score


class AbstentionEval(object):

    def __init__(self, metric, abstention_fraction):
        self.metric = metric
        self.abstention_fraction = abstention_fraction

    def __call__(self, abstention_scores, y_true, y_score):
        #lower abstention score means KEEP
        indices = np.argsort(abstention_scores)[
                    :int(np.ceil(len(y_true)*self.abstention_fraction))] 
        return self.metric(y_true=y_true[indices],
                           y_score=y_score[indices])


class AuPrcAbstentionEval(AbstentionEval):

    def __init__(self, abstention_fraction):
        super(AuPrcAbstentionEval, self).__init__(
            metric=average_precision_score,
            abstention_fraction=abstention_fraction)


class AuRocAbstentionEval(AbstentionEval):

    def __init__(self, abstention_fraction):
        super(AuRocAbstentionEval, self).__init__(
            metric=roc_auc_score,
            abstention_fraction=abstention_fraction)
    

class ThresholdFinder(object):

    def __call__(self, valid_labels, valid_posterior):
        raise NotImplementedError()


class FixedThreshold(ThresholdFinder):

    def __init__(self, threshold):
        self.threshold = threshold

    def __call__(self, valid_labels, valid_posterior):
        return self.threshold


class OptimalF1(ThresholdFinder):

    def __init__(self, beta,
                       range_to_search=np.arange(0.00, 1.00, 0.01),
                       verbose=True):
        self.beta = beta
        self.range_to_search = range_to_search
        self.verbose = verbose

    def __call__(self, valid_labels, valid_posterior):

        valid_labels = np.array(valid_labels) 
        total_positives = np.sum(valid_labels==1)

        best_score = -1
        best_threshold = 0
        for threshold in self.range_to_search:
            y_pred = np.array(valid_posterior > threshold)
            true_positives = np.sum(valid_labels*y_pred)
            predicted_positives = np.sum(y_pred)
            precision = float(true_positives)/\
                        (predicted_positives + np.finfo(np.float32).eps)
            recall = float(true_positives)/\
                        (total_positives + np.finfo(np.float32).eps)
            bb = self.beta ** 2
            score = ((1 + bb) * (precision * recall)) /\
                    (bb * precision + recall + np.finfo(np.float32).eps)
            if score > best_score:
                best_threshold = threshold
                best_score = score   
        if (self.verbose):
            print("Threshold is",best_threshold)
        return best_threshold 


class AbstainerFactory(object):

    def __call__(self, valid_labels,
                       valid_posterior,
                       valid_uncert):
        """
            Inputs: validation set labels, posterior probs, uncertainties
            Returns: a function that accepts posterior probs and
                        uncertainties and outputs the abstention scores,
                        where a low score = KEEP
        """
        raise NotImplementedError()


class NegPosteriorDistanceFromThreshold(AbstainerFactory):

    def __init__(self, threshold_finder):
        self.threshold_finder = threshold_finder

    def __call__(self, valid_labels, valid_posterior, valid_uncert=None):

        threshold = self.threshold_finder(valid_labels, valid_posterior)

        def abstaining_func(posterior_probs, uncertainties=None):
            return -np.abs(posterior_probs-threshold) 
        return abstaining_func


class MarginalDeltaMetric(AbstainerFactory):

    def __init__(self, proportion_positives, estimate_vals_from_valid=False):
        self.proportion_positives = proportion_positives
        self.estimate_vals_from_valid = estimate_vals_from_valid

    def compute_metric(self, y_true, y_score):
        raise NotImplementedError()

    def compute_abstention_score(self, est_metric, ppos, pos_cdf, neg_cdf,
                                       est_numpos, est_numneg):
        raise NotImplementedError()

    def __call__(self, valid_labels, valid_posterior, valid_uncert=None):

        #get the original auROC from the validation set
        est_metric = self.compute_metric(y_true=valid_labels,
                                         y_score=valid_posterior)

        #compute the cdf for the positives and the negatives
        num_positives = np.sum(valid_labels==1)
        num_negatives = np.sum(valid_labels==0)
        
        sorted_labels_and_probs = sorted(zip(valid_labels, valid_posterior),
                                         key=lambda x: x[1]) 
        running_sum_positives = [0]
        running_sum_negatives = [0]
        for label, prob in sorted_labels_and_probs:
            if (label==1):
                running_sum_positives.append(running_sum_positives[-1]+1)
                running_sum_negatives.append(running_sum_negatives[-1])
            else:
                running_sum_positives.append(running_sum_positives[-1])
                running_sum_negatives.append(running_sum_negatives[-1]+1)
        positives_cdf = np.array(running_sum_positives)/float(num_positives) 
        negatives_cdf = np.array(running_sum_negatives)/float(num_negatives) 

        #validation_vals are a 3-tuple of prob, positive_cdf, neg_cdf
        validation_vals = list(zip([x[1] for x in sorted_labels_and_probs],
                               positives_cdf, negatives_cdf))

        def abstaining_func(posterior_probs, uncertainties=None):
            #test_posterior_and_index have 2-tuples of prob, testing index
            test_posterior_and_index = [(x[1], x[0]) for x in
                                        enumerate(posterior_probs)]
            sorted_valid_and_test =\
                sorted(validation_vals+test_posterior_and_index,
                       key=lambda x: x[0])
            pos_cdf = 0
            neg_cdf = np.finfo(np.float32).eps
            test_sorted_posterior_probs = []
            test_sorted_pos_cdfs = []
            test_sorted_neg_cdfs = []
            test_sorted_indices = []
            to_return = np.zeros(len(posterior_probs))
            for value in sorted_valid_and_test:
                is_from_valid = True if len(value)==3 else False 
                if (is_from_valid):
                    pos_cdf = value[1]
                    neg_cdf = max(value[2],np.finfo(np.float32).eps)
                else:
                    ppos = value[0]
                    idx = value[1]
                    test_sorted_posterior_probs.append(ppos)
                    test_sorted_indices.append(idx)
                    test_sorted_pos_cdfs.append(pos_cdf)
                    test_sorted_neg_cdfs.append(neg_cdf)
            test_sorted_abstention_scores = self.compute_abstention_score(
                est_metric=est_metric,
                est_numpos=len(posterior_probs)*self.proportion_positives,
                est_numneg=len(posterior_probs)*(1-self.proportion_positives),
                ppos=np.array(test_sorted_posterior_probs),
                pos_cdfs=np.array(test_sorted_pos_cdfs),
                neg_cdfs=np.array(test_sorted_neg_cdfs))

            from matplotlib import pyplot as plt
            plt.plot(test_sorted_posterior_probs, test_sorted_abstention_scores)
            plt.show()

            final_abstention_scores = np.zeros(len(posterior_probs)) 
            final_abstention_scores[test_sorted_indices] =\
                test_sorted_abstention_scores 
            return final_abstention_scores

        return abstaining_func


class MarginalDeltaAuRoc(MarginalDeltaMetric):

    def compute_metric(self, y_true, y_score):
        return roc_auc_score(y_true=y_true, y_score=y_score)

    def compute_abstention_score(self, est_metric, est_numpos, est_numneg,
                                       ppos, pos_cdfs, neg_cdfs):
        return (ppos*((est_metric - neg_cdfs)/est_numpos) 
                + (1-ppos)*((est_metric - (1-pos_cdfs))/est_numneg))


class MarginalDeltaAuPrc(MarginalDeltaMetric):

    def compute_metric(self, y_true, y_score):
        return average_precision_score(y_true=y_true, y_score=y_score)

    def compute_abstention_score(self, est_metric, est_numpos, est_numneg,
                                       ppos, pos_cdfs, neg_cdfs):
        precision_at_threshold =\
            ((1-pos_cdfs)*est_numpos)/(
             (1-pos_cdfs)*est_numpos + (1-neg_cdfs)*est_numneg)
        slope_if_positive = (est_metric - precision_at_threshold)/est_numpos 
        ##at a given threshold, find the slope of the boost to precision from
        ##evicting a higher-ranked negative
        slope_evict_higher_negative = ppos*(1-pos_cdfs)/(
            ((1-pos_cdfs)*est_numpos + (1-neg_cdfs)*est_numneg)**2) 
        ##take the running sum of the boost to get the total
        ##slope of auPRC w.r.t. evicting a negative at that threshold
        slope_if_negative = np.cumsum(slope_evict_higher_negative)
        return slope_if_positive*ppos + slope_if_negative*(1-ppos)


class Uncertainty(AbstainerFactory):

    def __call__(self, valid_labels=None, valid_posterior=None,
                       valid_uncert=None):

        def abstaining_func(posterior_probs, uncertainties):
            #posterior_probs can be None
            return uncertainties
        return abstaining_func


class ConvexHybrid(AbstainerFactory):

    def __init__(self, factory1, factory2,
                       abstention_eval_func, stepsize=0.1,
                       verbose=True):
        self.factory1 = factory1
        self.factory2 = factory2
        self.abstention_eval_func = abstention_eval_func
        self.stepsize = stepsize
        self.verbose = verbose

    def __call__(self, valid_labels, valid_posterior, valid_uncert):

        factory1_func = self.factory1(valid_labels=valid_labels,
                                      valid_posterior=valid_posterior,
                                      valid_uncert=valid_uncert)
        factory2_func = self.factory2(valid_labels=valid_labels,
                                      valid_posterior=valid_posterior,
                                      valid_uncert=valid_uncert)

        def evaluation_func(scores):
            return self.abstention_eval_func(
                    abstention_scores=scores,
                    y_true=valid_labels,
                    y_score=valid_posterior)  

        a = find_best_mixing_coef(
                evaluation_func=evaluation_func,
                scores1=factory1_func(posterior_probs=valid_posterior,
                                      uncertainties=valid_uncert),
                scores2=factory2_func(posterior_probs=valid_posterior,
                                      uncertainties=valid_uncert),
                stepsize=self.stepsize)
       
        if (self.verbose):
            print("Best a",a) 

        def abstaining_func(posterior_probs, uncertainties):
            scores1 = factory1_func(posterior_probs=posterior_probs,
                                    uncertainties=uncertainties)
            scores2 = factory2_func(posterior_probs=posterior_probs,
                                   uncertainties=uncertainties)
            return a*scores1 + (1-a)*scores2
        return abstaining_func


def find_best_mixing_coef(evaluation_func, scores1, scores2, stepsize):

    assert stepsize > 0.0 and stepsize < 1.0
    coefs_to_try = np.arange(0.0, 1+stepsize, stepsize)

    best_objective = None
    best_a = 0
    for a in coefs_to_try:
        b = 1.0 - a
        scores = a*scores1 + b*scores2
        objective = evaluation_func(scores) 
        if (objective > best_objective or best_objective is None):
            best_objective = objective 
            best_a = a
    return best_a