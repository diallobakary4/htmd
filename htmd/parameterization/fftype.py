# (c) 2015-2018 Acellera Ltd http://www.acellera.com
# All Rights Reserved
# Distributed under HTMD Software License Agreement
# No redistribution in whole or part
#
import os
import logging
import subprocess
import unittest
from yaml import load as yamlload
from pickle import load as pickleload
from tempfile import TemporaryDirectory

import numpy as np
import parmed

from htmd.home import home
from htmd.molecule.molecule import Molecule
from htmd.parameterization.readers import readPREPI, readFRCMOD, readRTF

logger = logging.getLogger(__name__)


fftypemethods = ('CGenFF_2b6', 'GAFF', 'GAFF2')


def _canonicalizeAtomNames(mol):
    """
    This fixes up the atom naming and reside name to be consistent.
    NB this scheme matches what MATCH does.
    Don't change it or the naming will be inconsistent with the RTF.
    """

    mol = mol.copy()

    mol.segid[:] = 'L'
    logger.info('Rename segment to %s' % mol.segid[0])
    mol.resname[:] = 'MOL'
    logger.info('Rename residue to %s' % mol.resname[0])

    sufices = {}
    for i in range(mol.numAtoms):
        name = mol.element[i].upper()
        sufices[name] = sufices.get(name, 0) + 1
        name += str(sufices[name])

        logger.info('Rename atom %d: %-4s --> %-4s' % (i, mol.name[i], name))
        mol.name[i] = name

    return mol


def fftype(mol, rtfFile=None, prmFile=None, method='GAFF2', acCharges=None, tmpDir=None, netcharge=None):
    """
    Assing atom types and force field parameters for a given molecule.
    Additionally, atom masses and improper dihedral are set.
    Optionally, atom charges can be set if `acCharges` is set (see below).

    The assignment can be done:
      1. For CHARMM CGenFF_2b6 with MATCH (method = 'CGenFF_2b6');
      2. For AMBER GAFF with antechamber (method = 'GAFF');
      3. For AMBER GAFF2 with antechamber (method = 'GAFF2');

    Parameters
    ----------
    mol : Molecule
        Molecule to use for the assignment
    rtfFile : str
        Path to a RTF file from which to read the topology
    prmFile : str
        Path to a PRM file from which to read the parameters
    method : str
        Atomtyping assignment method.
        Use :func:`fftype.listFftypemethods <htmd.parameterization.fftype.listFftypemethods>` to get a list of available
        methods.
        Default: :func:`fftype.defaultFftypemethod <htmd.parameterization.fftype.defaultFftypemethod>`
    acCharges : str
        Optionally assign charges with antechamber. Check `antechamber -L` for available options.
        Note: only works for GAFF and GAFF2.
    tmpDir: str
        Directory for temporary files. If None, a directory is created and
        deleted automatically.
    netcharge : float
        The net charge of the molecule.

    Returns
    -------
    prm : :class:`ParameterSet <parmed.parameters.ParameterSet>` object
        Returns a parmed ParameterSet object with the parameters.
    mol : :class:`Molecule <htmd.molecule.molecule.Molecule>` object
        The modified Molecule object with the matching atom types for the ParameterSet
    """

    if method not in fftypemethods:
        raise ValueError('Invalid method {}. Available methods {}'.format(method, ','.join(fftypemethods)))

    if method == 'CGenFF_2b6' and acCharges:
        raise ValueError('acCharges')

    if netcharge is None:
        netcharge = int(round(np.sum(mol.charge)))
        logger.warning('Using atomic charges from molecule object to calculate net charge')

    if rtfFile and prmFile:
        logger.info('Reading FF parameters from {} and {}'.format(rtfFile, prmFile))
        prm = parmed.charmm.CharmmParameterSet(rtfFile, prmFile)
        names, elements, atomtypes, charges, masses, impropers = readRTF(rtfFile)

    else:
        logger.info('Assigning atom types with {}'.format(method))

        renamed_mol = _canonicalizeAtomNames(mol)

        # Create a temporary directory
        with TemporaryDirectory() as tmpdir:

            # HACK to keep the files
            tmpdir = tmpdir if tmpDir is None else tmpDir
            logger.debug('Temporary directory: {}'.format(tmpdir))

            if method in ('GAFF', 'GAFF2'):

                # Write the molecule to a file
                renamed_mol.write(os.path.join(tmpdir, 'mol.mol2'))

                atomtype = method.lower()

                # Run antechamber
                cmd = ['antechamber',
                       '-at', atomtype,
                       '-nc', str(netcharge),
                       '-fi', 'mol2',
                       '-i', 'mol.mol2',
                       '-fo', 'prepi',
                       '-o', 'mol.prepi']
                if acCharges is not None:
                    cmd += ['-c', acCharges]
                returncode = subprocess.call(cmd, cwd=tmpdir)
                if returncode != 0:
                    raise RuntimeError('"antechamber" failed')

                # Run parmchk2
                cmd = ['parmchk2',
                       '-f', 'prepi',
                       '-s', atomtype,
                       '-i', 'mol.prepi',
                       '-o', 'mol.frcmod',
                       '-a', 'Y']
                returncode = subprocess.call(cmd, cwd=tmpdir)
                if returncode != 0:
                    raise RuntimeError('"parmchk2" failed')

                # Read the results
                prm = parmed.amber.AmberParameterSet(os.path.join(tmpdir, 'mol.frcmod'))
                names, atomtypes, charges, impropers = readPREPI(renamed_mol, os.path.join(tmpdir, 'mol.prepi'))
                masses, elements = readFRCMOD(atomtypes, os.path.join(tmpdir, 'mol.frcmod'))

            elif method == 'CGenFF_2b6':

                # Write the molecule to a file
                renamed_mol.write(os.path.join(tmpdir, 'mol.pdb'))

                # Run match-type
                cmd = ['match-typer',
                       '-charge', str(netcharge),
                       '-forcefield', 'top_all36_cgenff_new',
                       'mol.pdb']
                returncode = subprocess.call(cmd, cwd=tmpdir)
                if returncode != 0:
                    raise RuntimeError('"match-typer" failed')

                prm = parmed.charmm.CharmmParameterSet(os.path.join(tmpdir, 'mol.rtf'), os.path.join(tmpdir, 'mol.prm'))
                names, elements, atomtypes, charges, masses, impropers = readRTF(os.path.join(tmpdir, 'mol.rtf'))

            else:
                raise ValueError('Invalid method {}'.format(method))

        assert np.all(renamed_mol.name == names)

    assert np.all(mol.element == elements)

    mol = mol.copy()
    mol.atomtype = atomtypes
    mol.masses = masses
    mol.impropers = impropers
    if acCharges is not None:
        mol.charge = charges

    return prm, mol


class TestFftype(unittest.TestCase):

    def setUp(self):
        self.refDir = home(dataDir='test-fftype')

    def assertListAlmostEqual(self, list1, list2, places=7):
        self.assertEqual(len(list1), len(list2))
        for a, b in zip(list1, list2):
            self.assertAlmostEqual(a, b, places=places)

    def _init_mol(self, molName, ffTypeMethod, chargetuple):

        molFile = os.path.join(self.refDir, '{}.mol2'.format(molName))

        if chargetuple == 'None':
            acCharges = None
            netcharge = None
        else:
            acCharges = chargetuple[0]
            netcharge = chargetuple[1]

        mol = Molecule(molFile)

        with TemporaryDirectory() as tmpDir:
            self.testParameters, self.testMolecule = fftype(mol,
                                                            method=ffTypeMethod,
                                                            acCharges=acCharges,
                                                            netcharge=netcharge,
                                                            tmpDir=tmpDir)
            self.testIntermediaryFiles = sorted(os.listdir(tmpDir))

    def _generate_references(self, name, method):
        import numbers
        import numpy
        from pickle import dump as pickledump

        def mapping(value):
            if isinstance(value, str):
                return '\'{}\''.format(value)
            elif isinstance(value, numbers.Real) or isinstance(value, numbers.Integral):
                return str(value)
            elif isinstance(value, numpy.ndarray):
                return '[{}]'.format(', '.join(map(mapping, list(value))))
            else:
                raise Exception('No mapping for type {}'.format(type(value)))

        print('Copy these to mol_props.yaml')
        for prop in ['name', 'element', 'atomtype', 'charge', 'impropers']:
            print('{}: {}'.format(prop if prop.endswith('s') else '{}s'.format(prop),
                                  '[{}]'.format(', '.join(map(mapping, getattr(self.testMolecule, prop))))))
        print('\nVerify these. They are already written through pickle')
        with open(os.path.join(self.refDir, name, method, 'params.p'), 'wb') as outfile:
            for i in self.testParameters.__dict__:
                print(i, getattr(self.testParameters, i))
            pickledump(self.testParameters, outfile)

        print('\nCopy these to intermediary_files.yaml')
        print('[{}]'.format(', '.join('\'{}\''.format(i) for i in self.testIntermediaryFiles)))

    def _test_mol_props(self, names, elements, atomtypes, charges, impropers):

        self.assertEqual(list(self.testMolecule.name), names)
        self.assertEqual(list(self.testMolecule.element), elements)
        self.assertEqual(list(self.testMolecule.atomtype), atomtypes)
        self.assertListAlmostEqual(list(self.testMolecule.charge), charges)
        if len(impropers) != 0:
            for test, ref in zip(list(self.testMolecule.impropers), impropers):
                self.assertEqual(list(test), ref)
        else:
            self.assertEqual(list(self.testMolecule.impropers), impropers)

    def _test_params(self, params):
        for i in self.testParameters.__dict__:
            self.assertEqual(getattr(self.testParameters, i), getattr(params, i))

    def _test_intermediary_files(self, files):
        self.assertEqual(self.testIntermediaryFiles, files)

    def test_mol(self):

        toTest = (
            ('ethanolamine', 'GAFF2', 'None'),
            ('1o5b_ligand', 'GAFF2', ('gas', 1)),
            ('1z95_ligand', 'GAFF2', ('gas', 0)),
            ('4gr0_ligand', 'GAFF2', ('gas', -1))
        )

        for name, method, chargetuple in toTest:
            with self.subTest(name=name, method=method, chargetuple=chargetuple):

                refDir = os.path.join(self.refDir, name, method)
                self._init_mol(name, method, chargetuple)

                with open(os.path.join(refDir, 'mol_props.yaml')) as infile:
                    refProps = yamlload(infile)
                self._test_mol_props(**refProps)

                with open(os.path.join(refDir, 'params.p'), 'rb') as infile:
                    refParams = pickleload(infile)
                self._test_params(refParams)

                with open(os.path.join(refDir, 'intermediary_files.yaml')) as infile:
                    refFiles = yamlload(infile)
                self._test_intermediary_files(refFiles)

    def test_broken_atomname(self):

        molFile = os.path.join(self.refDir, 'ethanolamine_wrongnames.mol2')

        mol = Molecule(molFile)

        with self.assertRaises(RuntimeError):
            fftype(mol, method='GAFF2')


if __name__ == '__main__':
    unittest.main(verbosity=2)