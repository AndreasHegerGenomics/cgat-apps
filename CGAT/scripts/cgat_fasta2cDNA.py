'''
cgat_fasta2cDNA.py - converting multi-fasta of exon features into a multi-fasta of spliced cDNAs/RNAs
======================================================================================================

:Tags: Python

Purpose
-------

Usage
-----

.. Example use case

Example::

   python cgat_fasta2cDNA.py

Type::

   python cgat_fasta2cDNA.py --help

for command line help.

Command line options
--------------------

'''

import sys
import CGATCore.Experiment as E
import CGATCore.IOTools as IOTools


def makeSplicedFasta(infile):
    '''
    Merge fasta sequences together into a single
    spliced transcript sequence
    '''

    fasta_dict = {}
    with IOTools.open_file(infile) as fafile:
        for line in fafile.readlines():
            if line[0] == '>':
                header = line.rstrip("\n")
                fasta_dict[header] = ''
            else:
                fasta_dict[header] += line.rstrip("\n")

    for key, value in sorted(fasta_dict.items()):
        yield "%s\n%s\n" % (key, value)


def main(argv=None):
    """script main.
    parses command line options in sys.argv, unless *argv* is given.
    """

    if argv is None:
        argv = sys.argv

    # setup command line parser
    parser = E.OptionParser(version="%prog version: $Id$",
                            usage=globals()["__doc__"])

    # add common options (-h/--help, ...) and parse command line
    (options, args) = E.start(parser, argv=argv)

    infile = argv[-1]
    for record in makeSplicedFasta(infile):
        options.stdout.write(record)

    # write footer and output benchmark information.
    E.stop()

if __name__ == "__main__":
    sys.exit(main(sys.argv))
