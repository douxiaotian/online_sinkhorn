from os.path import expanduser
from typing import Union

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from joblib import Parallel, Memory, delayed
from scipy.special import logsumexp

from online_sinkhorn.data import Subsampler


def compute_distance(x, y):
    x2 = np.sum(x ** 2, axis=1)
    y2 = np.sum(y ** 2, axis=1)
    return x2[:, None] + y2[None, :] - 2 * x @ y.T


class OT:
    def __init__(self, max_size, dimension, callback=None, step_size=1., step_size_exp=0.,
                 batch_size=1, batch_size_exp=0., avg_step_size=1., avg_step_size_exp=0.,
                 no_memory=False, full_update=False, max_updates: Union[str, int] = 'auto', averaging=False):
        self.max_size = max_size
        self.callback = callback
        self.dimension = dimension
        self.step_size = step_size
        self.step_size_exp = step_size_exp
        self.batch_size = batch_size
        self.batch_size_exp = batch_size_exp
        self.avg_step_size = avg_step_size
        self.avg_step_size_exp = avg_step_size_exp
        self.full_update = full_update
        self.max_updates = max_updates
        self.no_memory = no_memory

        self.averaging = averaging

        if no_memory and averaging:
            raise NotImplementedError

        # Attributes
        self.distance_ = np.zeros((max_size, max_size))
        self.x_ = np.empty((max_size, dimension))
        self.p_ = np.full((max_size, 1), fill_value=-np.float('inf'))
        self.y_ = np.empty((max_size, dimension))
        self.q_ = np.full((1, max_size), fill_value=-np.float('inf'))

        if self.averaging:
            self.avg_p_ = np.full((max_size, 1), fill_value=-np.float('inf'))
            self.avg_q_ = np.full((1, max_size), fill_value=-np.float('inf'))

        self.x_cursor_ = 0
        self.y_cursor_ = 0
        self.computations_ = 0
        self.n_updates_ = 0

    def _callback(self):
        if self.callback is not None:
            self.callback(self)

    def set_params(self, **params):
        for key, value in params:
            self.__setattr__(key, value)

    @property
    def is_full(self):
        return self.x_cursor_ == self.max_size or self.y_cursor_ == self.max_size

    def random_fit(self, *, x=None, y=None):
        if x is None and y is None:
            raise ValueError
        if x is not None:
            n = x.shape[0]
            self.x_cursor_ = n
            self.x_[:self.x_cursor_] = x
            distance = compute_distance(x, self.y_[:self.y_cursor_])
            f = - logsumexp(self.q_[:, :self.y_cursor_] - distance, axis=1,
                            keepdims=True)
        else:
            f = None
        if y is not None:
            m = y.shape[0]
            self.y_cursor_ = m
            self.y_[:self.y_cursor_] = y
            distance = compute_distance(self.x_[:self.x_cursor_], y)
            g = - logsumexp(self.p_[:self.x_cursor_, :] - distance, axis=0,
                            keepdims=True)
        else:
            g = None
        if f is not None:
            self.p_[:self.x_cursor_] = f - np.log(n)
        if g is not None:
            self.q_[:, self.x_cursor_] = g - np.log(m)
        self.n_updates_ += 1

    def partial_fit(self, *, x=None, y=None, step_size=1., full_update=False, avg_step_size=1.):
        if x is None and y is None:
            raise ValueError

        if x is not None:
            n = x.shape[0]
            new_x_cursor = min(self.x_cursor_ + n, self.max_size)
            n = new_x_cursor - self.x_cursor_
            x = x[:n]
            if x.shape[0] > 0:
                self.x_[self.x_cursor_:new_x_cursor] = x
                distance = compute_distance(x, self.y_[:self.y_cursor_])
                self.computations_ += self.y_cursor_ * n
                self.distance_[self.x_cursor_:new_x_cursor, :self.y_cursor_] = distance
                if full_update:
                    distance = self.distance_[:new_x_cursor, :self.y_cursor_]

                if self.y_cursor_ == 0:  # Init
                    if full_update:
                        f = np.zeros((new_x_cursor, 1))
                    else:
                        f = np.zeros((n, 1))
                else:
                    f = - logsumexp(self.q_[:, :self.y_cursor_] - distance, axis=1,
                                    keepdims=True)
                    self.computations_ += distance.shape[0] * distance.shape[1]
            else:
                f = None
        else:
            f = None
            new_x_cursor = self.x_cursor_
            n = None
        if y is not None:
            m = y.shape[0]
            new_y_cursor = min(self.y_cursor_ + m, self.max_size)
            m = new_y_cursor - self.y_cursor_
            y = y[:m]
            if y.shape[0] > 0:
                self.y_[self.y_cursor_:new_y_cursor] = y
                distance = compute_distance(self.x_[:self.x_cursor_], y)
                self.computations_ += self.x_cursor_ * m
                self.distance_[:self.x_cursor_, self.y_cursor_:new_y_cursor] = distance
                if full_update:
                    distance = self.distance_[:self.x_cursor_, :new_y_cursor]

                if self.x_cursor_ == 0:
                    if full_update:
                        g = np.zeros((1, new_y_cursor))
                    else:
                        g = np.zeros((1, m))
                else:
                    g = - logsumexp(self.p_[:self.x_cursor_, :] - distance, axis=0,
                                    keepdims=True)
                    self.computations_ += distance.shape[0] * distance.shape[1]
            else:
                g = None
        else:
            g = None
            new_y_cursor = self.y_cursor_
            m = None
        if x is not None and y is not None:
            self.distance_[self.x_cursor_:new_x_cursor, self.y_cursor_:new_y_cursor] = compute_distance(x, y)
            self.computations_ += n * m
        if f is not None:
            if full_update:
                self.p_[:new_x_cursor, :] = f - np.log(new_x_cursor)
            else:
                if step_size == 1:
                    self.p_[:self.x_cursor_, :] = - float('inf')
                else:
                    self.p_[:self.x_cursor_, :] += np.log(1 - step_size)
                self.p_[self.x_cursor_:new_x_cursor, :] = np.log(step_size) + f - np.log(n)
                if self.averaging:
                    if avg_step_size == 1.:
                        self.avg_p_[:new_x_cursor] = self.p_[:new_x_cursor]
                    else:
                        self.avg_p_[:new_x_cursor] = np.logaddexp(
                            self.avg_p_[:new_x_cursor] + np.log(1 - avg_step_size),
                            self.p_[:new_x_cursor] + np.log(avg_step_size))
            self.x_cursor_ = new_x_cursor
        if g is not None:
            if full_update:
                self.q_[:, :new_y_cursor] = g - np.log(new_y_cursor)
            else:
                if step_size == 1:
                    self.q_[:, :self.y_cursor_] = - float('inf')
                else:
                    self.q_[:, :self.y_cursor_] += np.log(1 - step_size)
                self.q_[:, self.y_cursor_:new_y_cursor] = np.log(step_size) + g - np.log(m)
                if self.averaging:
                    if avg_step_size == 1.:
                        self.avg_q_[:, :new_y_cursor] = self.q_[:, :new_y_cursor]
                    else:
                        self.avg_q_[:, :new_y_cursor] = np.logaddexp(
                            self.avg_q_[:, :new_y_cursor] + np.log(1 - avg_step_size),
                            self.q_[:, :new_y_cursor] + np.log(avg_step_size))
            self.y_cursor_ = new_y_cursor
        self.n_updates_ += 1

    def refit(self, *, refit_f=True, refit_g=True, step_size=1.):
        if self.x_cursor_ == 0 or self.y_cursor_ == 0:
            return
        if refit_f:
            # shape (:self.x_cursor, 1)
            f = - logsumexp(self.q_[:, :self.y_cursor_]
                            - self.distance_[:self.x_cursor_, :self.y_cursor_],
                            axis=1, keepdims=True)
            self.computations_ += self.x_cursor_ * self.y_cursor_
        else:
            f = None
        if refit_g:
            # shape (x_idx, 1)
            g = - logsumexp(self.p_[:self.x_cursor_, :]
                            - self.distance_[:self.x_cursor_, :self.y_cursor_],
                            axis=0, keepdims=True)
            self.computations_ += self.x_cursor_ * self.y_cursor_
        else:
            g = None

        if refit_f:
            if step_size == 1:
                self.p_[:self.x_cursor_, :] = f - np.log(self.x_cursor_)
            else:
                self.p_[:self.x_cursor_, :] = np.logaddexp(f - np.log(self.x_cursor_) + np.log(step_size),
                                                           self.p_[:self.x_cursor_] + np.log(1 - step_size))
        if refit_g:
            if step_size == 1:
                self.q_[:, :self.y_cursor_] = g - np.log(self.y_cursor_)
            else:
                self.q_[:, :self.y_cursor_] = np.logaddexp(g - np.log(self.x_cursor_) + np.log(step_size),
                                                           self.q_[:self.x_cursor_] + np.log(1 - step_size))
        self.n_updates_ += 1

    def compute_ot(self):
        if self.averaging:
            q, p = self.avg_q_, self.avg_p_
        else:
            q, p = self.q_, self.p_
        if self.no_memory:
            distance = compute_distance(self.x_[:self.x_cursor_], self.y_[:self.y_cursor_])
        else:
            distance = self.distance_[:self.x_cursor_, :self.y_cursor_]
        f = - logsumexp(q[:, :self.y_cursor_] - distance, axis=1, keepdims=True)
        g = - logsumexp(p[:self.x_cursor_] - distance, axis=0, keepdims=True)
        ff = - logsumexp(g - distance, axis=1, keepdims=True) + np.log(self.y_cursor_)
        gg = - logsumexp(f - distance, axis=0, keepdims=True) + np.log(self.x_cursor_)
        return (f.mean() + ff.mean() + g.mean() + gg.mean()) / 2

    def evaluate_potential(self, *, x=None, y=None):
        if self.averaging:
            q, p = self.avg_q_, self.avg_p_
        else:
            q, p = self.q_, self.p_
        if x is not None:
            distance = compute_distance(x, self.y_[:self.y_cursor_])
            if self.y_cursor_ == 0:
                f = np.zeros((x.shape[0], 1))
            else:
                f = - logsumexp(q[:, :self.y_cursor_] - distance, axis=1, keepdims=True)
        else:
            f = None
        if y is not None:
            distance = compute_distance(self.x_[:self.x_cursor_], y)
            if self.x_cursor_ == 0:
                g = np.zeros((1, y.shape[0]))
            else:
                g = - logsumexp(p[:self.x_cursor_, :] - distance, axis=0, keepdims=True)
        else:
            g = None
        if f is not None and g is not None:
            return f, g
        elif f is not None:
            return f
        elif g is not None:
            return g
        else:
            raise ValueError

    def reset(self, x, y):
        f, g = self.evaluate_potential(x=x, y=y)
        distance = compute_distance(x, y)
        self.computations_ += self.x_cursor_ * y.shape[0] + self.y_cursor_ * x.shape[0] + x.shape[0] * y.shape[0]
        self.x_cursor_ = x.shape[0]
        self.y_cursor_ = y.shape[0]
        self.x_[:self.x_cursor_] = x
        self.y_[:self.y_cursor_] = y
        self.distance_[:self.x_cursor_, :self.y_cursor_] = distance
        self.q_[:, :self.y_cursor_] = g - np.log(self.y_cursor_)
        self.p_[:self.x_cursor_, :] = f - np.log(self.x_cursor_)

        if self.averaging:
            self.avg_q_[:, :self.y_cursor_] = self.q_[:, :self.y_cursor_]
            self.avg_p_[:self.x_cursor_, :] = self.p_[:self.x_cursor_, :]

        self.n_updates_ = 0

    def online_sinkhorn_loop(self, x_sampler, y_sampler):
        while not self.is_full:
            if self.step_size_exp != 0:
                step_size = self.step_size / np.float_power((self.n_updates_ + 1), self.step_size_exp)
            else:
                step_size = self.step_size
            if self.avg_step_size != 0:
                avg_step_size = self.avg_step_size / np.float_power((self.n_updates_ + 1), self.avg_step_size_exp)
            else:
                avg_step_size = self.avg_step_size
            if self.batch_size_exp != 0:
                batch_size = np.ceil(
                    self.batch_size * np.float_power((self.n_updates_ + 1), self.batch_size_exp * 2)).astype(
                    int)
            else:
                batch_size = self.batch_size

            x, loga = x_sampler(batch_size)
            y, logb = y_sampler(batch_size)
            self.partial_fit(x=x, y=y, step_size=step_size, full_update=self.full_update, avg_step_size=avg_step_size)
            self._callback()
        return self

    def random_sinkhorn_loop(self, x_sampler, y_sampler):
        while self.n_updates_ < self.max_updates:
            if self.batch_size_exp != 0:
                batch_size = np.ceil(
                    self.batch_size * np.float_power((self.n_updates_ + 1), self.batch_size_exp * 2)).astype(
                    int)
            else:
                batch_size = self.batch_size

            x, loga = x_sampler(batch_size)
            y, logb = y_sampler(batch_size)
            self.random_fit(x=x, y=y)
            self._callback()
        return self

    def sinkhorn_loop(self):
        while self.n_updates_ < self.max_updates:
            if self.step_size_exp != 0:
                step_size = self.step_size / np.float_power((self.n_updates_ + 1), self.step_size_exp)
            else:
                step_size = self.step_size
            self.refit(refit_f=True, refit_g=True, step_size=step_size)
            w = self.compute_ot()
            self._callback()
        return self


def sinkhorn(x, y, max_updates, ref=None, step_size=1., step_size_exp=0., ):
    if ref is not None:
        callback = Callback(ref)
    else:
        callback = None
    ot = OT(dimension=x.shape[1], max_updates=max_updates, callback=callback, max_size=x.shape[0],
            step_size=step_size, step_size_exp=step_size_exp, averaging=False, no_memory=False)
    ot.reset(x, y)
    ot.sinkhorn_loop()
    return ot


def online_sinkhorn(x_sampler, y_sampler, max_size, ref=None, step_size=1., step_size_exp=0.,
                    batch_size=1, batch_size_exp=0., avg_step_size=1., avg_step_size_exp=0., averaging=False,
                    max_updates='auto', full_update=False, refine_updates=0):
    if ref is not None:
        callback = Callback(ref)
    else:
        callback = None
    ot = OT(dimension=x_sampler.dim, max_updates=max_updates, callback=callback,
            max_size=max_size,
            step_size=step_size, step_size_exp=step_size_exp,
            batch_size=batch_size, batch_size_exp=batch_size_exp, avg_step_size=avg_step_size,
            avg_step_size_exp=avg_step_size_exp, averaging=averaging,
            full_update=full_update,
            )
    ot.online_sinkhorn_loop(x_sampler, y_sampler)
    if refine_updates > 0:
        ot.set_params(max_updates=refine_updates, step_size=1., step_size_exp=0.,
                      averaging=False)
        ot.sinkhorn_loop()
    return ot


def random_sinkhorn(x_sampler, y_sampler, max_size, ref=None, step_size=1., step_size_exp=0.,
                    batch_size=1, batch_size_exp=0., averaging=False,
                    max_updates='auto', full_update=False, refine_updates=0):
    if ref is not None:
        callback = Callback(ref)
    else:
        callback = None
    ot = OT(dimension=x_sampler.dim, max_updates=max_updates, callback=callback,
            max_size=max_size,
            step_size=step_size, step_size_exp=step_size_exp, no_memory=no_memory,
            batch_size=batch_size, batch_size_exp=batch_size_exp, avg_step_size=avg_step_size,
            avg_step_size_exp=avg_step_size_exp, averaging=averaging,
            full_update=full_update,
            )
    ot.online_sinkhorn_loop(x_sampler, y_sampler)
    if refine_updates > 0:
        ot.set_params(max_updates=refine_updates, step_size=1., step_size_exp=0.,
                      averaging=False)
        ot.sinkhorn_loop()
    return ot


class Callback():
    def __init__(self, ref):
        self.trace = []
        self.ref = ref

    def __call__(self, ot):
        f, g = ot.evaluate_potential(x=self.ref['x'], y=self.ref['y'])
        w = ot.compute_ot()
        var_err = var_norm(f - self.ref['f']) + var_norm(g - self.ref['g'])
        w_err = np.abs(w - self.ref['w'])
        self.trace.append(dict(computations=ot.computations_, samples=ot.x_cursor_ + ot.y_cursor_,
                               iter=ot.n_updates_,
                               var_err=var_err, w_err=w_err, n_updates=ot.n_updates_))


def var_norm(x):
    return np.max(x) - np.min(x)


def run_OT():
    np.random.seed(0)

    n = 50
    ref_updates = 1000

    x = np.random.randn(n, 5)
    y = np.random.randn(n, 5) + 10

    mem = Memory(location=None)

    ot = mem.cache(sinkhorn)(x, y, ref_updates)
    f, g = ot.evaluate_potential(x=x, y=y)
    w = ot.compute_ot()
    ref = dict(f=f, g=g, x=x, y=y, w=w)

    x_sampler = Subsampler(x)
    y_sampler = Subsampler(y)

    jobs = []
    # for step_size, step_size_exp in [(1., 0.)]:
    #     jobs.append((f'Sinkhorn s={step_size}/t^{step_size_exp}',
    #                  delayed(mem.cache(sinkhorn))(x, y, ref=ref, step_size=step_size, step_size_exp=step_size_exp,
    #                                               max_updates=ref_updates)))
    jobs.append(
        ('Random Sinkhorn', delayed(mem.cache(online_sinkhorn))(x_sampler, y_sampler, ref=ref, max_size=10,
                                                                full_update=False, step_size=1., step_size_exp=0.,
                                                                max_updates=n * 10, batch_size=10, no_memory=True)))
    # for (step_size_exp, batch_size_exp) in ([1, 0.], [.5, .5], [0., 1.], [.5, 1], [.5, 1]):
    #     jobs.append(
    #         (f'Online Sinhkorn s=1/t^{step_size_exp} b=10 t^{2 * batch_size_exp}',
    #          delayed(mem.cache(online_sinkhorn))(x_sampler, y_sampler, max_size=n * 10,
    #                                              ref=ref, max_updates='auto',
    #                                              full_update=False, step_size=1.,
    #                                              step_size_exp=step_size_exp,
    #                                              batch_size=10, batch_size_exp=batch_size_exp,
    #                                              no_memory=False)))
    traces = Parallel(n_jobs=5)(job for (name, job) in jobs)
    dfs = []
    for ot, (name, job) in zip(traces, jobs):
        trace = ot.callback.trace
        df = pd.DataFrame(trace)
        df['name'] = name
        dfs.append(df)
    dfs = pd.concat(dfs, axis=0)
    dfs.to_pickle('results.pkl')


def plot_results():
    df = pd.read_pickle('results.pkl')
    fig, axes = plt.subplots(2, 3, sharex='col', sharey='row')
    for name, sub_df in df.groupby(by='name'):
        axes[0][0].plot(sub_df['computations'], sub_df['w_err'], label=name)
        axes[1][0].plot(sub_df['computations'], sub_df['var_err'], label=name)
        axes[0][2].plot(sub_df['iter'], sub_df['w_err'], label=name)
        axes[1][2].plot(sub_df['iter'], sub_df['var_err'], label=name)
        axes[0][1].plot(sub_df['samples'], sub_df['w_err'], label=name)
        axes[1][1].plot(sub_df['samples'], sub_df['var_err'], label=name)
    for ax in axes.ravel():
        ax.set_yscale('log')
        ax.set_xscale('log')
    axes[1][0].set_xlabel('Computations')
    axes[1][1].set_xlabel('Samples')
    axes[1][2].set_xlabel('Iteration')
    axes[0][0].set_ylabel('W err')
    axes[1][0].set_ylabel('Var err')
    # axes[0][0].set_ylim([1e-3, 1e2])
    # axes[1][0].set_ylim([1, 1e2])
    axes[1][1].legend()
    plt.show()


if __name__ == '__main__':
    run_OT()
    plot_results()
