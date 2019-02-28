""" This module implements several parametrized nonlinear process spectra.


Disclaimer
----------

THIS CODE IS FOR EDUCATIONAL PURPOSES ONLY! The code in this package was not
optimized for accuracy or performance. Rather it aims to provide a simple
implementation of the basic algorithms.

Author: Nils Geib, nils.geib@uni-jena.de
"""
import numpy as np
from . import lib
from . import io
from .mesh_data import MeshData
from .frequencies import convert


# global dictionary that contains all PNPS classes
_PNPS_CLASSES = {}


# =============================================================================
# Metaclass and factory
# =============================================================================
class MetaPNPS(type):
    """ Metaclass that registers PNPS classes in a global dictionary.
    """
    def __new__(cls, clsname, bases, attrs):
        global _PNPS_CLASSES
        newclass = super().__new__(cls, clsname, bases, attrs)
        processes, method = newclass._supported_processes, newclass.method
        if processes is None or method is None:
            return newclass
        # register the PNPS method name, e.g., FROG or MIIPS
        if method not in _PNPS_CLASSES:
            _PNPS_CLASSES[method] = {}
        dct = _PNPS_CLASSES[method]
        if isinstance(processes, str):
            processes = [processes]
        for p in processes:
            if p in dct:
                raise ValueError("%s-%s has two implementing classes!" %
                                 (p, method))
            dct[p] = newclass
        return newclass


def PNPS(pulse, method, process, **kwargs):
    """ Factory function to create PNPS instances.
    """
    method = method.lower()
    process = process.lower()
    try:
        cls = _PNPS_CLASSES[method][process]
    except KeyError:
        raise ValueError("PNPS method '%s-%s' is unknown!" % (process, method))
    return cls(pulse, process=process, **kwargs)


class MetaIOPNPS(io.MetaIO, MetaPNPS):
    # to fix metaclass conflicts
    pass


# =============================================================================
# PNPS Base class
# =============================================================================
class BasePNPS(io.IO, metaclass=MetaIOPNPS):
    """ The PNPS base class
    """
    process = None
    method = None
    _supported_processes = None
    parameter_name = ""
    parameter_unit = ""
    # io parameters
    _io_store = ['ft', 'w0', 'w', 'process']

    def __init__(self, pulse, process, **kwargs):
        self.ft = pulse.ft
        self.w0 = pulse.w0
        self.w = pulse.w
        self.process = process
        # put the keyword arguments in the local keyspace and add them
        # to the storage list.
        for key, value in kwargs.items():
            setattr(self, key, value)
            self._add_to_storage(key)
        # add the parameter name to storage
        if self.parameter_name != "":
            self._add_to_storage(self.parameter_name)
        self._post_init()

    def _post_init(self):
        if (self._supported_processes is not None and
                self.process not in self._supported_processes):
            raise ValueError("Nonlinear process `%s` is not supported." %
                             self.process)
        # calculate the fundamental wavelength
        self.wl = convert(self.w + self.w0, "om", "wl")
        # calculate the wavelength of the process spectrum
        if self.process == "shg":
            self.process_w = 2 * self.w0 + self.ft.w
        elif self.process == "thg":
            self.process_w = 3 * self.w0 + self.ft.w
        elif self.process == "sd":
            self.process_w = self.w0 + self.ft.w
        elif self.process == "pg":
            self.process_w = self.w0 + self.ft.w
        self.process_wl = convert(self.process_w, "om", "wl")
        # store intermediate results in a dictionary
        self._tmp = dict()

    @property
    def scheme(self):
        return self.process + "-" + self.method

    def measure(self, Sk):
        """ Simulates the measurement process.

        Note that we deal with the spectrum over the frequency!
        For retrieving from actual data we need to rescale this by lambda^2.
        """
        Sn = self.ft.forward(Sk)
        return lib.abs2(Sn)

    def calculate(self, spectrum, parameter):
        """ Calculates the PNPS signal S_mk and the trace T_mn.

        Parameters
        ----------
        spectrum : 1d-array
            The pulse spectrum for which the PNPS trace is calculated.
        parameter : scalar or 1d-array
            The PNPS parameter (array) for which the PNPS trace is calculated.

        Returns
        -------
        1d- or 2d-array
            Returns the calculated PNPS trace over the frequency
            ``self.process_w``. If parameter was a scalar a 1d-array is
            returned. If it was a 1d-array a 2d-array is returned where the
            parameter runs along the first axis and the frequency along the
            second.
        """
        parameter = np.atleast_1d(parameter)
        Tmn = np.zeros((parameter.size, spectrum.size))
        Smk = np.zeros((parameter.size, spectrum.size), dtype=np.complex128)
        for m, p in enumerate(parameter):
            Tmn[m, :], Smk[m, :] = self._calculate(spectrum, p)
        # if a scalar parameter was used, squeeze out one dimension
        Tmn = Tmn.squeeze()
        Smk = Smk.squeeze()
        # store for later use (in self.trace)
        self.Tmn = Tmn
        self.Smk = Smk
        self.parameter = parameter
        self.spectrum = spectrum
        return Tmn

    def gradient(self, Smk2, parameter):
        """ Calculates the gradient ∇_n Z_m.
        """
        parameter = np.atleast_1d(parameter)
        Smk2 = np.atleast_2d(Smk2)
        gradnZm = np.zeros((parameter.shape[0], Smk2.shape[1]),
                           dtype=np.complex128)
        for m, p in enumerate(parameter.flat):
            gradnZm[m, :] = self._gradient(Smk2[m, :], p)
        # if a scalar parameter is passed, squeeze out one dimension
        return gradnZm.squeeze()

    @property
    def trace(self):
        """ Returns the last calculated trace as a MeshData object.
        """
        return MeshData(self.Tmn, self.parameter, self.process_w,
                        labels=["frequency", self.parameter_name],
                        units=["Hz", self.parameter_unit])


# =============================================================================
# Collinear PNPS methods
# =============================================================================
class CollinearPNPS(BasePNPS):
    """ Implements collinear methods: d-scan, iFROG, etc.
    """
    _supported_processes = ["shg", "thg", "sd"]

    def _calculate(self, spectrum, parameter):
        """ Calculates the nonlinear process spectrum for a single parameter.

        Follows the notation from our paper.
        """
        ft = self.ft
        Hn = self.mask(parameter)
        Ck = ft.backward(Hn * spectrum)
        if self.process == "shg":
            Sk = Ck * Ck
        elif self.process == "thg":
            Sk = Ck * Ck * Ck
        elif self.process == "sd":
            Sk = lib.abs2(Ck) * Ck
        Tn = self.measure(Sk)
        # store intermediate results for later use in gradient
        self._tmp[parameter] = Hn, Ck, Sk
        return Tn, Sk

    def _gradient(self, Sk2, parameter):
        """ Returns the gradient of Z based on the previous call to _spectrum.
        """
        ft = self.ft
        # retrieve the intermediate results
        Hn, Ck, Sk = self._tmp[parameter]
        # difference between the updated PNPS signal and the original one
        dSk = Sk2 - Sk
        # calculate the gradients as described in the supplement
        if self.process == "shg":
            gradnZ = 2 * Hn.conj() * ft.forward(dSk * Ck.conj())
        elif self.process == "thg":
            gradnZ = 3 * Hn.conj() * ft.forward(dSk * (Ck * Ck).conj())
        elif self.process == "sd":
            gradnZ = Hn.conj() * ft.forward(dSk.conj() * Ck * Ck +
                                            2 * dSk * lib.abs2(Ck))
        # common scale for all gradients (note the minus)
        gradnZ *= -2.0 * lib.twopi * ft.dw / ft.dt
        return gradnZ


class MIIPS(CollinearPNPS):
    method = "miips"
    parameter_name = "delta"
    parameter_unit = "rad"

    def __init__(self, pulse, process, alpha, gamma):
        super().__init__(pulse, process, alpha=alpha, gamma=gamma)

    def mask(self, delta):
        w = self.ft.w + self.w0
        return np.exp(1.0j * self.alpha * np.cos(self.gamma * w - delta))


class IFROG(CollinearPNPS):
    method = "ifrog"
    parameter_name = "tau"
    parameter_unit = "s"

    def __init__(self, pulse, process):
        super().__init__(pulse, process)

    def mask(self, tau):
        w = self.ft.w + self.w0
        return 0.5 + 0.5 * np.exp(-1.0j * w * tau)


class DSCAN(CollinearPNPS):
    method = "dscan"
    parameter_name = "insertion"
    parameter_unit = "m"

    def __init__(self, pulse, process, material):
        super().__init__(pulse, process, material=material)

    def mask(self, insertion):
        w = self.ft.w + self.w0
        k = self.material.k(w, unit="om")
        return np.exp(1.0j * k * insertion)


# =============================================================================
# Noncollinear PNPS methods
# =============================================================================
class NoncollinearPNPS(BasePNPS):
    """ Implements non-collinear methods: FROG, TDP, etc.
    """
    pass


class FROG(NoncollinearPNPS):
    _supported_processes = ["shg", "pg"]
    method = "frog"
    parameter_name = "delay"
    parameter_unit = "s"

    def __init__(self, pulse, process):
        super().__init__(pulse, process)

    def _calculate(self, spectrum, parameter):
        """ Calculates the nonlinear process spectrum for a single parameter.

        Follows the notation from our paper.
        """
        ft = self.ft
        delay = np.exp(1.0j * parameter * ft.w)
        Ak = ft.backward(delay * spectrum)
        Ek = ft.backward(spectrum)
        if self.process == "shg":
            Sk = Ak * Ek
        elif self.process == "pg":
            Sk = lib.abs2(Ak) * Ek
        Tn = self.measure(Sk)
        # store intermediate results for later use in gradient
        self._tmp[parameter] = delay, Ak, Ek, Sk
        return Tn, Sk

    def _gradient(self, Sk2, parameter):
        """ Returns the gradient of Z based on the previous call to _spectrum.
        """
        ft = self.ft
        # retrieve the intermediate results
        delay, Ak, Ek, Sk = self._tmp[parameter]
        # difference between original and updated PNPS signal
        dSk = Sk2 - Sk
        # calculate the gradients as described in the supplement
        if self.process == "shg":
            gradnZ = (delay.conj() * ft.forward(dSk * Ek.conj()) +
                      ft.forward(dSk * Ak.conj()))
        elif self.process == "pg":
            gradnZ = (2 * delay.conj() *
                      ft.forward(Ak * np.real(dSk * Ek.conj())) +
                      ft.forward(dSk * lib.abs2(Ak)))
        # common scale for all gradients (note the minus)
        gradnZ *= -2.0 * lib.twopi * ft.dw / ft.dt
        return gradnZ


class TDP(NoncollinearPNPS):
    _supported_processes = ["shg"]
    method = "tdp"
    parameter_name = "delay"
    parameter_unit = "s"

    def __init__(self, pulse, process, center, width):
        super().__init__(pulse, process, center=center, width=width)

    def _calculate(self, spectrum, parameter):
        """ Calculates the nonlinear process spectrum for a single parameter.

        Follows the notation from our paper.
        """
        ft = self.ft
        # convert intensity fwhm to amplitude std deviation
        sigma = 0.5 * self.width / np.sqrt(np.log(2))
        delay = (np.exp(1.0j * parameter * ft.w) *
                 lib.gaussian(self.wl, x0=self.center, sigma=sigma))
        Ak = ft.backward(delay * spectrum)
        Ek = ft.backward(spectrum)
        Sk = Ak * Ek
        Tn = self.measure(Sk)
        # store intermediate results for later use in gradient
        self._tmp[parameter] = delay, Ak, Ek, Sk
        return Tn, Sk

    def _gradient(self, Sk2, parameter):
        """ Returns the gradient of Z based on the previous call to _spectrum.
        """
        ft = self.ft
        # retrieve the intermediate results
        delay, Ak, Ek, Sk = self._tmp[parameter]
        # difference between original and updated PNPS signal
        dSk = Sk2 - Sk
        # calculate the gradients as described in the supplement
        gradnZ = (delay.conj() * ft.forward(dSk * Ek.conj()) +
                  ft.forward(dSk * Ak.conj()))
        # common scale for all gradients (note the minus)
        gradnZ *= -2.0 * lib.twopi * ft.dw / ft.dt
        return gradnZ