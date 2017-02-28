"""
Test for the DM_simple engine.

This file is part of the PTYPY package.
    :copyright: Copyright 2014 by the PTYPY team, see AUTHORS.
    :license: GPLv2, see LICENSE for details.
"""

import unittest
from ptypy.test import test_utils as tu 
from ptypy import utils as u



class DMSimpleTest(unittest.TestCase):
    def test_DM_simple(self):
        engine_params = u.Param()
        engine_params.name = 'DM_simple'
        engine_params.fourier_relax_factor = 0.01
        engine_params.alpha = 1.0
        engine_params.numiter = 5
        tu.EngineTestRunner(engine_params)
if __name__ == "__main__":
    unittest.main()