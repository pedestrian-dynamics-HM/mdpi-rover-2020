import os

from scipy.stats import norm, normaltest

from SALib.analyze.sobol import create_Si_dict
import numpy
from sklearn.model_selection import ShuffleSplit
from sklearn import preprocessing

import matplotlib.pyplot as plt
import pandas as pd

from pykrige import UniversalKriging3D
from sklearn.metrics import r2_score

from forward_propagation_1_analyse.sensitivity_analysis import sobolanalysis
from utils.read_write_results import read_data, write_sobol_indices_to_file


def kriging(
    parameter_values,
    dissemination_time,
    ints=None,
    exact_vals=False,
    enable_plotting=False,
    write_statistics=False,
):

    parameter_values = parameter_values.to_numpy()
    dissemination_time = dissemination_time.to_numpy().ravel()

    if ints is None:
        ints = numpy.arange(len(dissemination_time))

    dissemination_time_log = numpy.log10(dissemination_time[ints])

    # The transformation of the data is actually not necessary,
    # because the Kriging pipeline scales the data itself.
    # However, we transform the data HERE to point this out.

    min_max_scaler = preprocessing.MinMaxScaler()
    m1 = min_max_scaler.fit(parameter_values[ints, 0].reshape(-1, 1))
    m2 = min_max_scaler.fit(parameter_values[ints, 1].reshape(-1, 1))
    m3 = min_max_scaler.fit(parameter_values[ints, 2].reshape(-1, 1))

    ok3d = UniversalKriging3D(
        m1.transform(parameter_values[ints, 0].reshape(-1, 1)).ravel(),
        m2.transform(parameter_values[ints, 1].reshape(-1, 1)).ravel(),
        m3.transform(parameter_values[ints, 2].reshape(-1, 1)).ravel(),
        dissemination_time_log,
        nlags=10,
        enable_plotting=False,
        variogram_model="linear",
        exact_values=exact_vals,
    )

    #Q1 = ok3d.get_statistics()[0]
    #if Q1 > (2 / numpy.sqrt(len(parameter_values) - 1)):
    #    raise ValueError(f"Q1 value is {Q1}.")

    dissemination_time_kriging_exp, ss3d = ok3d.execute(
        "points",
        m1.transform(parameter_values[:, 0].reshape(-1, 1)).ravel(),
        m2.transform(parameter_values[:, 1].reshape(-1, 1)).ravel(),
        m3.transform(parameter_values[:, 2].reshape(-1, 1)).ravel(),
    )
    dissemination_time_kriging = 10 ** dissemination_time_kriging_exp.data
    ss3d = 10** ss3d.data

    variogram_fit = numpy.array(
        [
            ok3d.lags,
            ok3d.variogram_function(ok3d.variogram_model_parameters, ok3d.lags),
            ok3d.semivariance,
        ]
    ).transpose()

    if write_statistics:

        df = pd.DataFrame(variogram_fit, columns=["lag", "residual", "semi-variance"])
        df.index.names = ["index"]
        df.to_csv("results/variogramFitPlot.dat", sep=" ", float_format="%.4f")

        residuals = pd.DataFrame(ok3d.get_epsilon_residuals(), columns=["ResidualVal"])
        residuals.index.names = ["index"]
        k2, p = normaltest(residuals.values)
        residuals.to_csv("results/variogramResiduals.dat", sep=" ", float_format="%.4f")

        Q1, Q2, cR = ok3d.get_statistics()

        f = open("results/variogramStats.dat", "w+")
        f.write("Statistics of the single surrogate approach\n \n")
        f.write(f"Q1 = {Q1} -> should be 0 according to Kitanadis \n")
        f.write(f"Q2 = {Q2} -> should be 1 according to Kitanadis \n")
        f.write(f"cR = {cR} -> should be 'small' according to Kitanadis \n")
        f.write(f"p-value (normal distributed): {p} \n")
        f.close()

    if enable_plotting:

        ok3d.print_statistics()
        ok3d.display_variogram_model()
        ok3d.plot_epsilon_residuals()

        fig = plt.figure(figsize=plt.figaspect(0.5))
        ax = fig.add_subplot(111, projection="3d")
        ax.scatter(
            parameter_values[:, 0],
            parameter_values[:, 1],
            dissemination_time,
            c="black",
        )
        ax.scatter(
            parameter_values[:, 0],
            parameter_values[:, 1],
            dissemination_time_kriging,
            c=parameter_values[:, 2],
        )
        plt.show(block=False)

    return dissemination_time_kriging, ss3d, ok3d.get_statistics()[0], ok3d.get_statistics()[1]


def use_sub_sets(number_samples, number_sets, train_size=0.3):
    rn = numpy.arange(number_samples)

    # The ShuffleSplit iterator will generate a user defined number of independent train / test dataset splits. Samples are first shuffled and then split into a pair of train and test sets.
    # https://scikit-learn.org/stable/modules/cross_validation.html#cross-validation
    kf = ShuffleSplit(n_splits=number_sets, train_size=train_size, random_state=42)
    sets = numpy.array([t for t, __ in kf.split(rn)]).T

    return sets


if __name__ == "__main__":

    models = 100 # use the same number of cross-validation models as used in the bootstrapping procedure
    conf_level = 0.95 # 5% / 95% confidence interval

    results = os.path.join(
        os.path.dirname(os.getcwd()), "forward_propagation_1/output_df"
    )
    parameter, qoi = read_data(results, enable_plotting=False)

    #sobol indices for surrogate system
    qoi_surr, ssd, __, __ = kriging(
        parameter,
        qoi,
        ints=numpy.arange(2000),
        exact_vals=False,
        enable_plotting=True,
        write_statistics=True,
    )
    Si = sobolanalysis(qoi_surr)
    Si["CoD"] = r2_score(y_true=qoi, y_pred=qoi_surr)
    write_sobol_indices_to_file(Si, filename="results/SobolIndicesSurrogate.dat")

    # use multiple models
    train_percentage = [0.25, 0.375, 0.5]
    exact = False

    for train_size in train_percentage:

        ints = use_sub_sets(len(parameter), number_sets=models*1000, train_size=train_size)
        regression_coef, S1_dist, S2_dist, ST_dist, = list(), list(), list(), list()

        failed = 0
        for ii in range(models):
            info = f"Compute model {ii+1} with [ {ints[0, ii]}... ]"
            print(f"Compute {info}")

            qoi2, ssd, Q1, Q2 = kriging(
                parameter, qoi, ints[:, ii], exact_vals=exact, enable_plotting=False
            )

            if Q1 > (2 / numpy.sqrt(len(parameter) - 1)):
                print(f"Skip {info}, because Quality is not sufficient.")
                failed += 1
                continue

            r2_fit = r2_score(
                y_true=qoi.to_numpy()[ints[:, ii]].ravel(), y_pred=qoi2[ints[:, ii]]
            )
            r2_pred = r2_score(y_true=qoi.to_numpy().ravel(), y_pred=qoi2)
            Si2 = sobolanalysis(qoi2)

            regression_coef.append(r2_pred)
            S1_dist.append(Si2["S1"])
            S2_dist.append(Si2["S2"])
            ST_dist.append(Si2["ST"])

            if (ii + 1 - failed) != len(regression_coef):
                raise ValueError("Model with low quality not removed successfully.")


        regression_coef = numpy.array(regression_coef)
        S1_dist = numpy.array(S1_dist)
        S2_dist = numpy.array(S2_dist)
        ST_dist = numpy.array(ST_dist)

        Si_surrogate = create_Si_dict(D=3, calc_second_order=True)

        Z = norm.ppf(0.5 +  conf_level / 2)
        Si_surrogate["S1"] = S1_dist.mean(axis=0)
        Si_surrogate["S1_conf"] = Z * S1_dist.std(ddof=1, axis=0)

        Si_surrogate["S2"] = S2_dist.mean(axis=0)
        Si_surrogate["S2_conf"] = Z * S2_dist.std(ddof=1, axis=0)

        Si_surrogate["ST"] = ST_dist.mean(axis=0)
        Si_surrogate["ST_conf"] = Z * ST_dist.std(ddof=1, axis=0)

        Si_surrogate["CoD"] = regression_coef.mean()
        Si_surrogate["CoD_conf"] = Z * regression_coef.std(ddof=1)

        write_sobol_indices_to_file(
            Si_surrogate, f"results/IndicesSurrogates_{exact}_{train_size}.dat"
        )
