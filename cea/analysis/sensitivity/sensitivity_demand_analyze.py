"""
Analyze the results in the samples folder and write them out to an Excel file. This script assumes:

- a samples folder with the files `samples.npy` and `problem.pickle` was created with `sensitivity_demand_samples.py`
- all the results have been added to the samples folder in the format `result.%i.csv`, with `%i` replaced by the index
  into the samples array. Use the script `sensitivity_demand_simulate.py` to create the results.
- each result file has the same list of columns (the `--output-parameters` for the simulations were the same)
- the `analyze_sensitivity` function is called with the same method and arguments as the sampling routine
  (`sensitivity_demand_samples.py`).
"""
import os
import numpy as np
import pickle

import pandas as pd
from SALib.analyze import sobol
from SALib.analyze import morris

__author__ = "Jimeno A. Fonseca; Daren Thomas"
__copyright__ = "Copyright 2016, Architecture and Building Systems - ETH Zurich"
__credits__ = ["Jimeno A. Fonseca", "Daren Thomas"]
__license__ = "MIT"
__version__ = "0.1"
__maintainer__ = "Daren Thomas"
__email__ = "cea@arch.ethz.ch"
__status__ = "Production"


def analyze_sensitivity(samples_path, temporal_scale):
    """
    Run the analysis for each output parameter. The exact function to use is selected by the `method` parameter:
    Use "morris" for `SALib.analyze.morris` and "sobol" for `SALib.analyze.sobol`.

    The samples folder `samples_path` contains the results of running a simulation with `sensitivity_demand_simulate.py`
    for each sample generated by `sensitivity_demand_samples.py`.

    It also contains the `samples.npy` file with input values for the samples.
    It also contains the `problem.pickle` file which contains information about the problem as well as parameters
    that were set in the `sensitivity_demand_samples.py` call (`method`, `N` and method-specific parameters)

    :param samples_path: the path to the samples folder as created by `sensitivity_demand_samples.py`
    :type samples_path: str
    :param temporal_scale: The scale at which to do the analysis. Valid values: ``['monthly', 'yearly']``
    :type temporal_scale: str
    """
    print("analyzing sensitivity in %(samples_path)s for temporal_scale=%(temporal_scale)s" % locals())
    # do checks
    with open(os.path.join(samples_path, 'problem.pickle'), 'r') as f:
        problem = pickle.load(f)
    method = problem['method']
    assert method in ('sobol', 'morris'), "Invalid analysis method: %s" % method

    # choose which analysis function to use and the list of keys in the analysis output to store
    if method == 'sobol':
        analysis_variables = ['S1', 'ST', 'ST_conf']
        analysis_function = sobol_analyze_function
    else:
        # method == 'morris'
        analysis_variables = ['mu_star', 'sigma', 'mu_star_conf']
        analysis_function = morris_analyze_function

    # import The NumPy matrix containing the model inputs or samples
    samples = np.load(os.path.join(samples_path, 'samples.npy'))
    output_parameters = np.load(os.path.join(samples_path, 'output_parameters.npy'))
    samples_count = len(samples)

    # run the analysis for every input parameter
    for output_parameter in output_parameters:
        # create excel writer
        folder = os.path.join(samples_path, 'results')
        if not os.path.exists(folder):
            os.makedirs(folder)

        if temporal_scale == 'monthly':
            # temporal_scale = monthly
            writer = pd.ExcelWriter(
                os.path.join(folder,
                             'analysis_%s_%i_%s_%s.xls' % (method, problem['N'], output_parameter, temporal_scale)))

            for month in range(12):

                # read the results and get back a matrix m = buildings, n = samples.
                simulation_results = read_results(samples_path, samples_count, output_parameter, temporal_scale, month)

                # run the analysis for every building and store it in a list
                analysis_results = [analysis_function(problem, samples, simulation_result) for simulation_result in
                                    simulation_results]

                # write out a worksheet for each analysis result (e.g. 'S1', 'ST', 'ST_conf' for method == 'sobol')
                for analysis_variable in analysis_variables:
                    worksheet_name = str(month + 1) + '_' + analysis_variable  # for every building
                    building_results = [result[analysis_variable] for result in analysis_results]
                    pd.DataFrame(building_results, columns=problem['names']).to_excel(writer, worksheet_name)
            writer.save()
        else: #run default
            writer = pd.ExcelWriter(
                os.path.join(folder, 'analysis_%s_%i_%s.xls' % (method, problem['N'], output_parameter)))

            # read the results and get back a matrix m = buildings, n = samples.
            simulation_results = read_results(samples_path, samples_count, output_parameter, temporal_scale, month=0)

            # run the analysis for every building and store it in a list
            analysis_results = [analysis_function(problem, samples, simulation_result) for simulation_result in
                                simulation_results]

            # clear some memory:
            simulation_results = None
            # write out a worksheet for each analysis result (e.g. 'S1', 'ST', 'ST_conf' for method == 'sobol')
            for analysis_variable in analysis_variables:
                worksheet_name = analysis_variable
                building_results = [result[analysis_variable] for result in analysis_results]
                pd.DataFrame(building_results, columns=problem['names']).to_excel(writer, worksheet_name)
            writer.save()

    print 'Sensitivity analysis results saved to %s' % folder

def sobol_analyze_function(problem, _, Y):
    """
    Use the SALib.analyze.sobol method to analyze the simulation results.

    :param problem: The definition of the problem statement as defined for the sampling method.
    :param _: placeholder for the `X` parameter of the morris method not used for sobol
    :param Y: The NumPy array containing the model outputs
    :param parameters: dictionary containing the key 'calc_second_order' with a bool value to be passed to the
                       SALib.analyze.morris `calc_second_order` parameter.
    :return: returns the result of the SALib.analyze.sobol method (from the documentation: a dictionary with keys
             `S1`, `S1_conf`, `ST`, and `ST_conf`, where each entry is a list of size D (the number of parameters)
             containing the indices in the same order as the parameter file. If calc_second_order is True, the
             dictionary also contains keys `S2` and `S2_conf`.)
    """
    assert 'calc_second_order' in problem, 'sobol method requires the `calc_second_order` parameter to be set (bool)'
    return sobol.analyze(problem, Y, calc_second_order=problem['calc_second_order'])


def morris_analyze_function(problem, X, Y):
    """
    Use the SALib.analyze.morris method to analyze the simulation results.

    :param problem: The definition of the problem statement as defined for the sampling method.
    :param X: the `X` parameter of the morris method (The NumPy matrix containing the model inputs)
    :param Y: The NumPy array containing the model outputs
    :param parameters: dictionary containing the keys 'grid_jump' and 'num_levels' that are passed on to the
                       SALib.analyze.morris method parameters of the same name.
    :return: returns the result of the SALib.analyze.sobol method (from the documentation: a dictionary with keys 
             `mu`, `mu_star`, `sigma`, and `mu_star_conf`, where each entry is a list of size D (the number of eters) 
             containing the indices in the same order as the parameter file.)
    """
    assert 'grid_jump' in problem, 'morris method requires the `grid_jump` parameter to be set (int)'
    assert 'num_levels' in problem, 'morris method requires the `num_levels` parameter to be set (int)'
    return morris.analyze(problem, X, Y,
                          grid_jump=problem['grid_jump'], num_levels=problem['num_levels'])


def read_results(samples_folder, samples_count, output_parameter, temporal_scale, month = 0):
    """
    Read each `results.%i.csv` file from the samples folder into a DataFrame and return them as a list. Each such
    csv file has a column for each output parameter specified for the simulation runs and a row for each building.

    :param samples_folder: path to the samples folder containing the results files of the simulation runs
    :type samples_folder: str

    :param samples_count: the total number of `results.%i.csv` files in the samples folder. Use
                          `sensitivity_demand_count.py` to calculate this for a given samples folder.
    :type samples_count: int

    :param output_parameter: output parameter of the simualation e.g., Qhsf, Ef
    :type samples_count: str

    :param output_parameter: output parameter of the simualation e.g., Qhsf, Ef
    :type samples_count: str

    :returns: matrix of lenght mxn where m: samples_count, n: buildings, content: result for output_parameter
    :rtype: list of DataFrame
    """
    if temporal_scale == 'yearly':
        results = np.array(
            [pd.read_csv(os.path.join(samples_folder, 'result.%i.csv' % i))[output_parameter].values for i in
             (range(samples_count))]).T
    else:
        num_buildings = pd.read_csv(os.path.join(samples_folder, 'result.0.csv')).shape[0]
        results = range(samples_count)
        for sample in range(samples_count):
            results[sample] = [pd.read_csv(
                os.path.join(samples_folder, 'result.%i.%i.csv' % (sample, building))).loc[month, output_parameter] for
                               building in range(num_buildings)]
        results = np.array(results).T
    return results


if __name__ == '__main__':
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument('-S', '--samples-folder', default='.',
                        help='folder to place the output files (samples.npy, problem.pickle) in')
    parser.add_argument('-t', '--temporal-scale', default='yearly', choices=['yearly', 'monthly'],
                        help='temporal scale of analysis (monthly or yearly)')
    args = parser.parse_args()
    analyze_sensitivity(samples_path=args.samples_folder, temporal_scale=args.temporal_scale)
