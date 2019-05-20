# -*- coding: utf-8 -*-
"""
Base engine. Used to define reconstruction parameters that are shared
by all engines.

This file is part of the PTYPY package.

    :copyright: Copyright 2014 by the PTYPY team, see AUTHORS.
    :license: GPLv2, see LICENSE for details.
"""
import numpy as np
import time
from .. import utils as u
from ..utils import parallel
from ..utils.verbose import logger, headerline, log
from ..utils.descriptor import EvalDescriptor
from .posref import AnnealingRefine
import gc

__all__ = ['BaseEngine', 'Base3dBraggEngine', 'DEFAULT_iter_info', 'PositionCorrectionEngine']

DEFAULT_iter_info = u.Param(
    iteration=0,
    iterations=0,
    engine='None',
    duration=0.,
    error=np.zeros((3,))
)

class BaseEngine(object):
    """
    Base reconstruction engine.
    In child classes, overwrite the following methods for custom behavior :
    engine_initialize
    engine_prepare
    engine_iterate
    engine_finalize
    Defaults:
    [numiter]
    default = 20
    type = int
    lowlim = 1
    help = Total number of iterations
    [numiter_contiguous]
    default = 1
    type = int
    lowlim = 1
    help = Number of iterations without interruption
    doc = The engine will not return control to the caller until this number of iterations is completed (not processing server requests, I/O operations, ...).
    [probe_support]
    default = 0.7
    type = float
    lowlim = 0.0
    help = Valid probe area as fraction of the probe frame
    doc = Defines a circular area centered on the probe frame, in which the probe is allowed to be nonzero.
    """

    # Define with which models this engine can work.
    COMPATIBLE_MODELS = []

    def __init__(self, ptycho, pars=None):
        """
        Base reconstruction engine.
        Parameters
        ----------
        ptycho : Ptycho
            The parent :any:`Ptycho` object.
        pars: Param or dict
            Initialization parameters
        """
        self.ptycho = ptycho

        p = self.DEFAULT.copy()
        if pars is not None:
            p.update(pars)
        self.p = p

        self.finished = False
        self.numiter = self.p.numiter

        # Instance attributes
        self.curiter = None
        self.alliter = None

        self.di = None
        self.ob = None
        self.pr = None
        self.ma = None
        self.ex = None
        self.pods = None

        self.probe_support = None
        self.t = None
        self.error = None

    def initialize(self):
        """
        Prepare for reconstruction.
        """
        logger.info('\n' +
                    headerline('Starting %s-algorithm.'
                               % str(type(self).__name__), 'l', '=') + '\n')
        logger.info('Parameter set:')
        logger.info(u.verbose.report(self.p, noheader=True).strip())
        logger.info(headerline('', 'l', '='))

        self.curiter = 0
        if self.ptycho.runtime.iter_info:
            self.alliter = self.ptycho.runtime.iter_info[-1]['iterations']
        else:
            self.alliter = 0

        # Common attributes for all reconstructions
        self.di = self.ptycho.diff
        self.ob = self.ptycho.obj
        self.pr = self.ptycho.probe
        self.ma = self.ptycho.mask
        self.ex = self.ptycho.exit
        self.pods = self.ptycho.pods

        self.probe_support = {}
        # Call engine specific initialization
        self.engine_initialize()

    def prepare(self):
        """
        Last-minute preparation before iterating.
        """
        self.finished = False
        # Calculate probe support
        # an individual support for each storage is calculated in saved
        # in the dict self.probe_support
        supp = self.p.probe_support
        if supp is not None:
            for name, s in self.pr.storages.iteritems():
                sh = s.data.shape
                ll, xx, yy = u.grids(sh, FFTlike=False)
                support = (np.pi * (xx**2 + yy**2) < supp * sh[1] * sh[2])
                self.probe_support[name] = support

        # Make sure all the pods are supported
        for label_, pod_ in self.pods.iteritems():
            if not pod_.model.__class__ in self.SUPPORTED_MODELS:
                raise Exception('Model %s not supported by engine' % pod_.model.__class__)

        # Call engine specific preparation
        self.engine_prepare()

    def iterate(self, num=None):
        """
        Compute one or several iterations.
        num : None, int number of iterations.
            If None or num<1, a single iteration is performed.
        """
        # Several iterations
        if self.p.numiter_contiguous is not None:
            niter_contiguous = self.p.numiter_contiguous
        else:
            niter_contiguous = 1

        # Overwrite default parameter
        if num is not None:
            niter_contiguous = num

        # Support numiter == 0 for debugging purposes
        if self.numiter == 0:
            self.finished = True

        if self.finished:
            return

        # For benchmarking
        self.t = time.time()

        it = self.curiter

        # Call engine specific iteration routine
        # and collect the per-view error.
        self.error = self.engine_iterate(niter_contiguous)

        # Check if engine did things right.
        if it >= self.curiter:

            logger.warn("""Engine %s did not increase iteration counter
            `self.curiter` internally. Accessing this attribute in that
            engine is inaccurate""" % self.__class__.__name__)

            self.curiter += niter_contiguous

        elif self.curiter != (niter_contiguous + it):

            logger.error("""Engine %s increased iteration counter
            `self.curiter` by %d instead of %d. This may lead to
            unexpected behaviour""" % (self.__class__.__name__,
            self.curiter-it, niter_contiguous))

        else:
            pass

        self.alliter += niter_contiguous

        if self.curiter >= self.numiter:
            self.finished = True

        # Prepare runtime
        self._fill_runtime()

        parallel.barrier()

    def _fill_runtime(self):
        local_error = u.parallel.gather_dict(self.error)
        if local_error:
            error = np.array(local_error.values()).mean(0)
        else:
            error = np.zeros((1,))
        info = dict(
            iteration=self.curiter,
            iterations=self.alliter,
            engine=type(self).__name__,
            duration=time.time() - self.t,
            error=error
        )

        self.ptycho.runtime.iter_info.append(info)
        self.ptycho.runtime.error_local = local_error

    def finalize(self):
        """
        Clean up after iterations are done.
        """
        self.engine_finalize()
        pass

    def engine_initialize(self):
        """
        Engine-specific initialization.
        Called at the end of self.initialize().
        """
        raise NotImplementedError()

    def engine_prepare(self):
        """
        Engine-specific preparation.
        Last-minute initialization providing up-to-date information for
        reconstruction. Called at the end of self.prepare()
        """
        raise NotImplementedError()

    def engine_iterate(self, num):
        """
        Engine single-step iteration.
        All book-keeping is done in self.iterate(), so this routine only needs
        to implement the "core" actions.
        """
        raise NotImplementedError()

    def engine_finalize(self):
        """
        Engine-specific finalization.
        Used to wrap-up engine-specific stuff. Called at the end of
        self.finalize()
        """
        raise NotImplementedError()



#local_tree = EvalDescriptor('')
#@local_tree.parse_doc('engine.common')
class PositionCorrectionEngine(BaseEngine):
    """
    A sub class engine that supports position correction

    Defaults:

    [position_refinement]
    default = False
    type = Param, bool
    help = If True refine scan positions

    [position_refinement.start]
    default = None
    type = int
    help = Number of iterations until position refinement starts

    [position_refinement.stop]
    default = None
    type = int
    help = Number of iterations after which positon refinement stops
    doc = If None, position refinement stops after last iteration

    [position_refinement.cycle]
    default = 1
    type = int
    help = Frequency of position refinement cycle

    [position_refinement.nshifts]
    default = 4
    type = int
    help = Number of random shifts calculated in each position refinement step (has to be multiple of 4)

    [position_refinement.amplitude]
    default = 0.001
    type = float
    help = Distance from original position per random shift [m]

    [position_refinement.max_shift]
    default = 0.002
    type = float
    help = Maximum distance from original position [m]

    """

    def initialize(self):
        """
        Prepare for reconstruction.
        """
        super(PositionCorrectionEngine, self).initialize()
        if (self.p.position_refinement.start is None) and (self.p.position_refinement.stop is None):
            self.do_position_refinement = False
        else:
            self.do_position_refinement = True
            self.ptycho.citations.add_article(
                title='An annealing algorithm to correct positioning errors in ptychography',
                author='Maiden et al.',
                journal='Ultramicroscopy',
                volume=120,
                year=2012,
                page=64,
                doi='10.1016/j.ultramic.2012.06.001',
                comment='Position Refinement using annealing algorithm',
            )
            if self.p.position_refinement.stop is None:
                self.p.position_refinement.stop = self.p.numiter

    def position_update(self):
        """
        Position refinement update.
        """
        if self.do_position_refinement is False:
            return


        do_update_pos = (self.p.position_refinement.stop > self.curiter >= self.p.position_refinement.start)
        do_update_pos &= (self.curiter % self.p.position_refinement.cycle) == 0

        shape = self.pr.S.values()[0].data[0].shape
        # Only used for calculating the shifted pos
        temp_ob = self.ob.copy()
        # Start position refinement
        if self.curiter == self.p.position_refinement.start:
            initial_positions = {}
            for dname, di_view in self.di.views.iteritems():
                initial_positions[dname] = di_view.pod.ob_view.coord
            self.position_refinement = AnnealingRefine(self.p.position_refinement, initial_positions, shape, temp_ob)

        # Update positions
        if do_update_pos:
            """
            Iterates trough all positions and refines them by a given algorithm. 
            """
            log(4, "----------- START POS REF -------------")

            # List of refined coordinates which will be used to reformat the object
            new_coords = {}

            # Maximum shift

            self.position_refinement.max_shift_dist = self.position_refinement.max_shift_dist_rule(self.curiter)
            for sname, storage in self.position_refinement.temp_ob.storages.iteritems():
                log(4, "Old storage shape is: %s" % str(storage.shape))
                storage.padding = int(np.round(self.position_refinement.max_shift_dist/ self.position_refinement.psize)) + 1
                storage.reformat()
                log(4, "new storage shape is: %s" % str(storage.shape))

            # Iterate through all diffraction views
            for dname, di_view in self.di.views.iteritems():
                # Check for new coordinates
                if di_view.active:
                    new_coords[dname] = self.position_refinement.update_view_position(di_view)

            # MPI reduce and update new coordinates
            new_coords = parallel.allreduce(new_coords)
            for dname, di_view in self.di.views.iteritems():
                if new_coords[dname][0] != 0 and new_coords[dname][1] != 0:
                    log(4, "Old coordinate (%s): " % (dname) + str(di_view.pod.ob_view.coord))
                    log(4, "New coordinate (%s): " % (dname) + str(new_coords[dname]))
                    di_view.pod.ob_view.coord = new_coords[dname]

            # Update object based on new position coordinates
            self.ob.reformat()

            # The size of the object might have been changed
            del self.ptycho.containers[self.ob.ID + '_vcover']
            del self.ptycho.containers[self.ob.ID + '_nrm']
            self.ob_viewcover = self.ob.copy(self.ob.ID + '_vcover', fill=0.)
            self.ob_nrm = self.ob.copy(self.ob.ID + '_nrm', fill=0.)
            for name, s in self.ob_viewcover.storages.iteritems():
                s.fill(s.get_view_coverage())

            # clean up
            del self.ptycho.containers[temp_ob.ID]
            del temp_ob
            gc.collect()




class Base3dBraggEngine(BaseEngine):
    """
    3d Bragg engines need a slightly different prepare() method, because
    a 2d probe support makes no sense (at least not yet...)

    Defaults:

    [probe_support]
    default = None
    """

    def prepare(self):
        """
        Last-minute preparation before iterating.
        """
        self.finished = False
        # Simple 2d probe support isn't applicable to the 3d case.
        supp = self.p.probe_support
        if supp is not None:
            raise NotImplementedError

        # Make sure all the pods are supported
        for label_, pod_ in self.pods.iteritems():
            if not pod_.model.__class__ in self.SUPPORTED_MODELS:
                raise Exception('Model %s not supported by engine' % pod_.model.__class__)

        # Call engine specific preparation
        self.engine_prepare()
