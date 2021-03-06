from __future__ import division, print_function, absolute_import
import numpy as np
from sklearn.metrics import roc_auc_score
#from sklearn.metrics import average_precision_score
import sys


def basic_average_precision_score(y_true, y_score):
    y_true = y_true.squeeze()
    #sort by y_score
    sorted_y_true, sorted_y_score = zip(*sorted(zip(y_true, y_score),
                                                 key=lambda x: x[1]))
    sorted_y_true = np.array(sorted_y_true).astype("float64")
    num_pos = np.sum(sorted_y_true)
    num_neg = np.sum(1-sorted_y_true)
    num_pos_above = num_pos - np.cumsum(sorted_y_true)
    num_neg_above = num_neg - np.cumsum(1-sorted_y_true)
    num_pos_above[-1] = 1.0
    num_neg_above[-1] = 0.0
    precisions = num_pos_above/(num_pos_above+num_neg_above).astype("float64")
    average_precision = np.sum(sorted_y_true*precisions)/(num_pos)
    return average_precision

average_precision_score = basic_average_precision_score


class AbstentionEval(object):

    def __init__(self, metric, proportion_to_retain):
        self.metric = metric
        self.proportion_to_retain = proportion_to_retain

    def __call__(self, abstention_scores, y_true, y_score):
        #lower abstention score means KEEP
        indices = np.argsort(abstention_scores)[
                    :int(np.ceil(len(y_true)*self.proportion_to_retain))] 
        return self.metric(y_true=y_true[indices],
                           y_score=y_score[indices])


class AuPrcAbstentionEval(AbstentionEval):

    def __init__(self, proportion_to_retain):
        super(AuPrcAbstentionEval, self).__init__(
            metric=average_precision_score,
            proportion_to_retain=proportion_to_retain)


class AuRocAbstentionEval(AbstentionEval):

    def __init__(self, proportion_to_retain):
        super(AuRocAbstentionEval, self).__init__(
            metric=roc_auc_score,
            proportion_to_retain=proportion_to_retain)
    

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
            sys.stdout.flush()
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


class MulticlassWrapper(AbstainerFactory):

    def __init__(self, single_class_abstainer_factory, verbose=True):
        self.single_class_abstainer_factory = single_class_abstainer_factory
        self.verbose = verbose

    def __call__(self, valid_labels, valid_posterior, valid_uncert):

        all_class_abstainers = []
        for class_idx in range(valid_labels.shape[1]):

            if (valid_labels is not None):
                class_valid_labels = valid_labels[:, class_idx] 
            else:
                class_valid_labels = None 

            if (valid_posterior is not None):
                class_valid_posterior = valid_posterior[:, class_idx]
            else:
                class_valid_posterior = None

            if (valid_uncert is not None):
                class_valid_uncert = valid_uncert[:, class_idx]
            else:
                class_valid_uncert = None
           
            class_abstainer = self.single_class_abstainer_factory(
                                        valid_labels=class_valid_labels,
                                        valid_posterior=class_valid_posterior,
                                        valid_uncert=class_valid_uncert) 
            all_class_abstainers.append(class_abstainer)

        def func(posterior_probs, uncertainties):

            all_class_scores = []

            for class_idx in range(posterior_probs.shape[1]):

                if (posterior_probs is not None):
                    class_posterior_probs = posterior_probs[:,class_idx]
                else:
                    class_posterior_probs = None

                if (uncertainties is not None):
                    class_uncertainties = uncertainties[:, class_idx]
                else:
                    class_uncertainties = None

                class_scores = all_class_abstainers[class_idx](
                                 posterior_probs=class_posterior_probs,
                                 uncertainties=class_uncertainties)
                all_class_scores.append(class_scores)
            return np.array(all_class_scores).transpose((1,0))

        return func
            

class RandomAbstention(AbstainerFactory):

    def __call__(self, valid_labels=None, valid_posterior=None,
                       valid_uncert=None):

        def random_func(posterior_probs, uncertainties=None):
            return np.random.permutation(range(len(posterior_probs)))/(
                     len(posterior_probs))
        return random_func


class NegPosteriorDistanceFromThreshold(AbstainerFactory):

    def __init__(self, threshold_finder):
        self.threshold_finder = threshold_finder

    def __call__(self, valid_labels, valid_posterior, valid_uncert=None):

        threshold = self.threshold_finder(valid_labels, valid_posterior)

        def abstaining_func(posterior_probs, uncertainties=None):
            return -np.abs(posterior_probs-threshold) 
        return abstaining_func


class NegativeAbsLogLikelihoodRatio(AbstainerFactory):

    def __call__(self, valid_labels, valid_posterior, valid_uncert=None):

        p_pos = np.sum(valid_labels)/len(valid_labels)
        assert p_pos > 0 and p_pos < 1.0, "only one class in labels"
        #lpr = log posterior ratio
        lpr = np.log(p_pos) - np.log(1-p_pos)

        def abstaining_func(posterior_probs, uncertainties=None):
            #llr = log-likelihood ratio
            # prob = 1/(1 + e^-(llr + lpr))
            # (1+e^-(llr + lpr)) = 1/prob
            # e^-(llr + lpr) = 1/prob - 1
            # llr + lpr = -np.log(1/prob - 1)
            # llr = -np.log(1/prob - 1) - lpr
            np.clip(posterior_probs, a_min=1e-7, a_max=None, out=posterior_probs)
            llr = -np.log(1/(posterior_probs) - 1) - lpr
            return -np.abs(llr)
        return abstaining_func


class RecursiveMarginalDeltaMetric(AbstainerFactory):

    def __init__(self, proportion_to_retain):
        self.proportion_to_retain = proportion_to_retain

    def estimate_metric(self, ppos, pos_cdfs, neg_cdfs):
        raise NotImplementedError()

    def compute_metric(self, y_true, y_score):
        raise NotImplementedError()

    def compute_abstention_score(self, est_metric, ppos, pos_cdf, neg_cdf,
                                       est_numpos, est_numneg):
        raise NotImplementedError()

    def __call__(self, valid_labels=None,
                       valid_posterior=None, valid_uncert=None):

        def abstaining_func(posterior_probs, uncertainties=None):
            reverse_eviction_ordering = np.zeros(len(posterior_probs))
            #test_posterior_and_index have 2-tuples of prob, testing index
            test_posterior_and_index = [(x[1], x[0]) for x in
                                        enumerate(posterior_probs)]
            test_sorted_indices, test_sorted_posterior_probs =\
                zip(*sorted(enumerate(posterior_probs),
                      key=lambda x: x[1]))
            test_sorted_posterior_probs =\
                np.array(test_sorted_posterior_probs)

            items_remaining = len(posterior_probs)  
            while items_remaining >\
                  int(self.proportion_to_retain*len(posterior_probs)):
                if (items_remaining%100 == 0):
                    print("Items recursively evicted:",
                      (len(posterior_probs)-items_remaining),
                       "of",len(posterior_probs)-
                       int(self.proportion_to_retain*len(posterior_probs)))
                    sys.stdout.flush()
                est_numpos_from_data = np.sum(test_sorted_posterior_probs)
                est_numneg_from_data = np.sum(1-test_sorted_posterior_probs)
                est_pos_cdfs_from_data =\
                    (np.cumsum(test_sorted_posterior_probs))/\
                    est_numpos_from_data
                est_neg_cdfs_from_data =\
                    (np.cumsum(1-test_sorted_posterior_probs))/\
                    est_numneg_from_data
                est_metric_from_data=self.estimate_metric(
                    ppos=test_sorted_posterior_probs,
                    pos_cdfs=est_pos_cdfs_from_data,
                    neg_cdfs=est_neg_cdfs_from_data)

                test_sorted_abstention_scores = self.compute_abstention_score(
                    est_metric=est_metric_from_data,
                    est_numpos=est_numpos_from_data,
                    est_numneg=est_numneg_from_data,
                    ppos=test_sorted_posterior_probs,
                    pos_cdfs=est_pos_cdfs_from_data,
                    neg_cdfs=est_neg_cdfs_from_data)
                to_evict_idx = max(zip(test_sorted_indices,
                                       test_sorted_abstention_scores),
                                   key=lambda x: x[1])[0]
                reverse_eviction_ordering[to_evict_idx] = items_remaining  
                items_remaining -= 1
                idx_to_evict_from_sorted =\
                    np.argmax(test_sorted_abstention_scores)
                test_sorted_indices =\
                    np.array(list(test_sorted_indices[:
                                   idx_to_evict_from_sorted])
                           +list(test_sorted_indices[
                                   idx_to_evict_from_sorted+1:]))
                test_sorted_posterior_probs =\
                    np.array(list(test_sorted_posterior_probs[:
                                   idx_to_evict_from_sorted])
                         +list(test_sorted_posterior_probs[
                                   idx_to_evict_from_sorted+1:]))
            return reverse_eviction_ordering

        return abstaining_func


class MarginalDeltaMetric(AbstainerFactory):

    def __init__(self, estimate_cdfs_from_valid=False,
                       estimate_imbalance_and_perf_from_valid=False,
                       all_estimates_from_valid=False):
        self.all_estimates_from_valid = all_estimates_from_valid
        if (self.all_estimates_from_valid):
            estimate_cdfs_from_valid = True
            estimate_imbalance_and_perf_from_valid = True
        self.estimate_cdfs_from_valid = estimate_cdfs_from_valid
        self.estimate_imbalance_and_perf_from_valid =\
             estimate_imbalance_and_perf_from_valid

    def estimate_metric(self, ppos, pos_cdfs, neg_cdfs):
        raise NotImplementedError()

    def compute_metric(self, y_true, y_score):
        raise NotImplementedError()

    def compute_abstention_score(self, est_metric, ppos, pos_cdf, neg_cdf,
                                       est_numpos, est_numneg):
        raise NotImplementedError()

    def __call__(self, valid_labels, valid_posterior, valid_uncert=None):

        if (self.all_estimates_from_valid):
            print("Estimating everything relative to validation set")

        #get the original auROC from the validation set
        valid_est_metric = np.array(self.compute_metric(
                                         y_true=valid_labels,
                                         y_score=valid_posterior))
        valid_num_positives = np.sum(valid_labels==1)
        valid_num_negatives = np.sum(valid_labels==0)

        #compute the cdf for the positives and the negatives from valid set
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
        valid_positives_cdf =\
            np.array(running_sum_positives)/float(valid_num_positives) 
        valid_negatives_cdf =\
            np.array(running_sum_negatives)/float(valid_num_negatives) 

        #validation_vals are a 3-tuple of prob, positive_cdf, neg_cdf
        validation_vals = list(zip([x[1] for x in sorted_labels_and_probs],
                               valid_positives_cdf, valid_negatives_cdf))


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
            test_sorted_posterior_probs = np.array(test_sorted_posterior_probs)
            test_sorted_pos_cdfs = np.array(test_sorted_pos_cdfs)
            test_sorted_neg_cdfs = np.array(test_sorted_neg_cdfs)

            valid_frac_pos = valid_num_positives/\
                             (valid_num_positives+valid_num_negatives)
            valid_frac_neg = valid_num_negatives/\
                             (valid_num_positives+valid_num_negatives)
            if (self.all_estimates_from_valid):
                est_numpos_from_valid = valid_num_positives
                est_numneg_from_valid = valid_num_negatives
            else:
                est_numpos_from_valid = valid_frac_pos*len(posterior_probs)
                est_numneg_from_valid = valid_frac_neg*len(posterior_probs)
            
            est_numpos_from_data = np.sum(test_sorted_posterior_probs)
            est_numneg_from_data = np.sum(1-test_sorted_posterior_probs)
            est_pos_cdfs_from_data =\
                (np.cumsum(test_sorted_posterior_probs))/est_numpos_from_data
            est_neg_cdfs_from_data =\
                (np.cumsum(1-test_sorted_posterior_probs))/est_numneg_from_data

            sorted_idx_and_val = sorted(enumerate(posterior_probs),
                                        key=lambda x: x[1])

            if (self.estimate_cdfs_from_valid):
                est_metric_from_data=self.estimate_metric(
                    ppos=test_sorted_posterior_probs,
                    pos_cdfs=test_sorted_pos_cdfs,
                    neg_cdfs=test_sorted_neg_cdfs)
            else:
                est_metric_from_data=self.estimate_metric(
                    ppos=test_sorted_posterior_probs,
                    pos_cdfs=est_pos_cdfs_from_data,
                    neg_cdfs=est_neg_cdfs_from_data)

            print("valid est metric", valid_est_metric)
            print("data est metric", est_metric_from_data)
            sys.stdout.flush()
            if (np.abs(est_metric_from_data-valid_est_metric) > 0.01):
                print("If the perf on the validation set is "
                      "very different from the estimated perf "
                      "on the test data, it may be a sign that "
                      "the calibration is poor!!!") 
                sys.stdout.flush()

            test_sorted_abstention_scores = self.compute_abstention_score(
                est_metric=(valid_est_metric if
                            self.estimate_imbalance_and_perf_from_valid
                            else est_metric_from_data),
                est_numpos=(est_numpos_from_valid if
                            self.estimate_imbalance_and_perf_from_valid
                            else est_numpos_from_data),
                est_numneg=(est_numneg_from_valid if
                            self.estimate_imbalance_and_perf_from_valid else
                            est_numneg_from_data),
                ppos=np.array(test_sorted_posterior_probs),
                pos_cdfs=(np.array(test_sorted_pos_cdfs)
                          if self.estimate_cdfs_from_valid
                          else est_pos_cdfs_from_data),
                neg_cdfs=(np.array(test_sorted_neg_cdfs)
                          if self.estimate_cdfs_from_valid
                          else est_neg_cdfs_from_data)
            )

            final_abstention_scores = np.zeros(len(posterior_probs)) 
            final_abstention_scores[test_sorted_indices] =\
                test_sorted_abstention_scores 
            return final_abstention_scores

        return abstaining_func


class AbstractMarginalDeltaMetricMixin(object):

    def estimate_metric(self, ppos, pos_cdfs, neg_cdfs):
        raise NotImplementedError()

    def compute_metric(self, y_true, y_score):
        raise NotImplementedError()

    def compute_abstention_score(self, est_metric, est_numpos, est_numneg,
                                       ppos, pos_cdfs, neg_cdfs):
        raise NotImplementedError()


class MarginalDeltaAuRocMixin(AbstractMarginalDeltaMetricMixin):

    def estimate_metric(self, ppos, pos_cdfs, neg_cdfs): 
        #probability that a randomly chosen positive is ranked above
        #a randomly chosen negative:
        est_total_positives = np.sum(ppos)
        #probability of being ranked above a randomly chosen negative
        #is just neg_cdf
        return np.sum(ppos*neg_cdfs)/est_total_positives

    def compute_metric(self, y_true, y_score):
        return roc_auc_score(y_true=y_true, y_score=y_score)

    def compute_abstention_score(self, est_metric, est_numpos, est_numneg,
                                       ppos, pos_cdfs, neg_cdfs):
        return (ppos*((est_metric - neg_cdfs)/est_numpos) 
                + (1-ppos)*((est_metric - (1-pos_cdfs))/est_numneg))


class MarginalDeltaAuRoc(MarginalDeltaAuRocMixin, MarginalDeltaMetric):
    pass


class RecursiveMarginalDeltaAuRoc(MarginalDeltaAuRocMixin,
                                  RecursiveMarginalDeltaMetric):
    pass


class MarginalDeltaAuPrcMixin(AbstractMarginalDeltaMetricMixin):

    def estimate_metric(self, ppos, pos_cdfs, neg_cdfs): 
        #average precision over all the positives
        num_pos = np.sum(ppos)
        num_neg = np.sum(1-ppos)
        #num positives ranked above = (1-pos_cdfs)*num_pos
        #num negatives ranked above = (1-neg_cdfs)*num_neg
        pos_cdfs[-1] = np.finfo(np.float32).eps #prevent div by 0
        precision_at_threshold = ((1-pos_cdfs)*num_pos)/\
                                 ((1-pos_cdfs)*num_pos + (1-neg_cdfs)*num_neg)
        precision_at_threshold[-1] = 1.0
        return np.sum(ppos*precision_at_threshold)/num_pos

    def compute_metric(self, y_true, y_score):
        return average_precision_score(y_true=y_true, y_score=y_score)

    def compute_abstention_score(self, est_metric, est_numpos, est_numneg,
                                       ppos, pos_cdfs, neg_cdfs):
        pos_cdfs[-1] = -1.0 #prevent 0.0 warning
        neg_cdfs[-1] = -1.0
        precision_at_threshold =\
            ((1-pos_cdfs)*est_numpos)/(
             (1-pos_cdfs)*est_numpos + (1-neg_cdfs)*est_numneg)
        precision_at_threshold[-1] = 1.0 #dealing with 0.0/0.0

        est_nneg_above = est_numneg*(1-neg_cdfs)
        est_npos_above = est_numpos*(1-pos_cdfs)
        #to prevent 0/0:
        est_npos_above[-1] = 1.0
        est_nneg_above[-1] = 0.0

        #mep_pos = marginal effect on precision of evicting higher
        #ranked positive example
        #mep_pos = -est_nneg_above/np.square(est_npos_above + est_nneg_above) 
        #mep_neg = est_npos_above/np.square(est_npos_above + est_nneg_above)
        #cmep_pos = np.cumsum(ppos*mep_pos)
        #cmep_neg = np.cumsum(ppos*mep_neg)

        #slope_if_positive =\
        #    (est_metric - precision_at_threshold + cmep_pos)/est_numpos
        #slope_if_negative = cmep_neg/est_numpos

        #return slope_if_positive*ppos + slope_if_negative*(1-ppos)

        mcpr_term1 = est_npos_above/np.square(est_npos_above + est_nneg_above)
        cmcpr_term1 = np.cumsum(ppos*mcpr_term1)
        mcpr_term2 = -1.0/(est_npos_above + est_nneg_above)
        cmcpr_term2 = np.cumsum(ppos*mcpr_term2)*ppos
        slope = (ppos*(est_metric - precision_at_threshold)
                 + cmcpr_term1 + cmcpr_term2)/est_numpos

        return slope



class MarginalDeltaAuPrc(MarginalDeltaAuPrcMixin, MarginalDeltaMetric):
    pass


class RecursiveMarginalDeltaAuPrc(MarginalDeltaAuPrcMixin,
                                  RecursiveMarginalDeltaMetric):
    pass


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
        if (best_objective is None or objective > best_objective):
            best_objective = objective 
            best_a = a
    return best_a

