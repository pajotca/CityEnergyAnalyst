# -*- coding: utf-8 -*-
"""
=========================================
heating radiators
=========================================

"""
from __future__ import division
from scipy.optimize import newton
import math


__author__ = "Jimeno A. Fonseca"
__copyright__ = "Copyright 2015, Architecture and Building Systems - ETH Zurich"
__credits__ = ["Jimeno A. Fonseca"]
__license__ = "MIT"
__version__ = "0.1"
__maintainer__ = "Daren Thomas"
__email__ = "thomas@arch.ethz.ch"
__status__ = "Production"


from numba.pycc import CC
cc = CC('calc_radiator')


@cc.export('fh', "f8(f8, f8, f8, f8, f8, f8, f8)")
def fh(x, mCw0, k2, Qh0, tair, LMRT, nh):
    Eq = mCw0 * k2 - Qh0 * (k2 / (math.log((x + k2 - tair) / (x - tair)) * LMRT)) ** (nh + 1)
    return Eq

@cc.export('lmrt', "f8(f8, f8, f8)")
def lmrt(tair0, trh0, tsh0):
    LMRT = (tsh0 - trh0) / math.log((tsh0 - tair0) / (trh0 - tair0))
    return LMRT

def calc_radiator(Qh, tair, Qh0, tair0, tsh0, trh0):
    nh = 0.3 #radiator constant
    if Qh > 0:
        tair = tair + 273
        tair0 = tair0 + 273
        tsh0 = tsh0 + 273
        trh0 = trh0 + 273
        mCw0 = Qh0 / (tsh0 - trh0)
        # minimum
        LMRT = lmrt(tair0, trh0, tsh0)
        k1 = 1 / mCw0
        k2 = Qh * k1
        result = newton(fh, trh0, args=(mCw0, k2, Qh0, tair, LMRT, nh), maxiter=100, tol=0.01) - 273
        trh = result.real
        tsh = trh + k2
        mCw = Qh / (tsh - trh)
    else:
        mCw = 0
        tsh = 0
        trh = 0
    return tsh, trh, mCw # C, C, W/C

try:
    # import Numba AOT versions of the functions above
    from calc_radiator import fh, lmrt
except ImportError:
    # fall back to using the python version
    print 'failed to import from calc_radiator, falling back to python functions'
    pass



if __name__ == '__main__':
    cc.compile()
