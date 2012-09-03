################################################################################
#
#   MRC FGU Computational Genomics Group
#
#   $Id$
#
#   Copyright (C) 2009 Andreas Heger
#
#   This program is free software; you can redistribute it and/or
#   modify it under the terms of the GNU General Public License
#   as published by the Free Software Foundation; either version 2
#   of the License, or (at your option) any later version.
#
#   This program is distributed in the hope that it will be useful,
#   but WITHOUT ANY WARRANTY; without even the implied warranty of
#   MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#   GNU General Public License for more details.
#
#   You should have received a copy of the GNU General Public License
#   along with this program; if not, write to the Free Software
#   Foundation, Inc., 59 Temple Place - Suite 330, Boston, MA  02111-1307, USA.
#################################################################################
'''
bed2table.py - annotate intervals
=================================

:Author: Andreas Heger
:Release: $Id$
:Date: |today|
:Tags: Python

Purpose
-------

This script takes a bed-formatted file as input and annotates each interval.
Possible annotators are (see option '--counter'):

overlap
    compute overlap with intervals in other bed file. If the other bed 
    file contains tracks, the overlap is computed per track.

peaks
    compute peak location in intervals. Requires one or more bam-files. This
    counter can also count within an secondary set of bam-files (--control-bam-file)
    and add this to the output.

Usage
-----

Example::

   python bed2table.py --help

Type::

   python bed2table.py --help

for command line help.

Documentation
-------------

Code
----

'''
import os, sys, string, re, optparse, math, time, tempfile, subprocess, types, bisect, array, collections
import GFF, GTF, Bed
import Experiment as E
import IndexedFasta
import Stats
import SequenceProperties
import Genomics
import Intervals
import numpy
import IndexedGenome
import pysam

class Counter( object ):
    
    def __init__(self, *args, **kwargs ):
        pass

class CounterOverlap( Counter ):
    '''count overlap for each interval in tracks.'''

    def __init__(self, filename, *args, **kwargs ):
        
        assert filename != None, "please supply filename for CounterOverlap"

        Counter.__init__(self, *args, **kwargs )

        self.filename = filename

        E.info( "reading intervals from %s" % self.filename )

        self.index = Bed.readAndIndex( open( self.filename, "r"),
                                       per_track = True )
        
        E.info( "read intervals for %s tracks" % len(self.index) )

        self.tracks = self.index.keys()
        self.headers = []
        for track in self.tracks:
            self.headers.extend( ["%s_nover" % track, "%s_bases" % track] )
            
    
    def update( self, bed ):
        '''update internal counts.'''

        results = []
        for track in self.tracks:
            try:
                overlaps = [ (x[0],x[1]) for x in self.index[track][bed.contig].find( bed.start, bed.end ) ]
            except KeyError:
                overlaps = []

            results.append( (len(overlaps), 
                             Intervals.calculateOverlap( [(bed.start, bed.end),],
                                                         Intervals.combine( overlaps ) ) ) )

        self.data = results

    def __str__(self):
        '''output overlap of interval in *bed*'''
                        
        r = []
        for track, result in zip(self.tracks, self.data):
            r.append( "\t".join( (str(result[0]), str(result[1])) ) )

        return "\t".join(r)

##-----------------------------------------------------------------------------------
CounterPeaksResult = collections.namedtuple( "CounterPeaksResult", ("length nreads avgval peakval npeaks peakcenter" ) )
class CounterPeaks(Counter):
    '''compute number of extent of peaks in an interval.'''

    headers = None

    def __init__(self, 
                 bamfiles, 
                 offsets, 
                 control_bamfiles, 
                 control_offsets, *args, **kwargs ):
        Counter.__init__(self, *args, **kwargs )
        if not bamfiles: raise ValueError("supply --bam-file options for readcoverage")

        assert len(offsets) == 0 or len(bamfiles) == len(offsets), "number of bamfiles not the same as number of offsets"
        assert len(control_offsets) == 0 or len(control_bamfiles) == len(control_offsets), "number of control bamfiles not the same as number of offsets"

        self.bamfiles = bamfiles
        self.offsets = offsets
        self.control_bamfiles = control_bamfiles
        self.control_offsets = control_offsets

        self.headers = list(CounterPeaksResult._fields)
        if self.control_bamfiles:
            self.headers.extend( ["control_%s" % x for x in CounterPeaksResult._fields] )

    def count( self, bed, bamfiles, offsets ):
        '''count reads in bed interval.'''

        contig, start, end = bed.contig, bed.start, bed.end

        length = end - start
        counts = numpy.zeros( length )
        nreads = 0

        if offsets:
            # if offsets are given, shift tags. 
            for samfile, offset in zip(bamfiles,offsets):

                if offset == 0: E.warn("0 offset will result in no data!" )
                shift = offset / 2
                # for peak counting I follow the MACS protocoll,
                # see the function def __tags_call_peak in PeakDetect.py
                # In words
                # Only take the start of reads (taking into account the strand)
                # add d/2=offset to each side of peak and start accumulate counts.
                # for counting, extend reads by offset
                # on + strand shift tags upstream
                # i.e. look at the downstream window
                xstart, xend = max(0, start - shift), max(0, end + shift)

                for read in samfile.fetch( contig, xstart, xend ):
                    nreads += 1
                    pos = read.pos
                    # some reads are assigned to a contig and position, but
                    # are flagged as unmapped - these might not have an alen attribute.
                    if read.is_unmapped: continue

                    if read.is_reverse:
                        # offset = 2 * shift
                        rstart = read.pos + read.alen - offset
                    else: 
                        rstart = read.pos + shift

                    rend = rstart + shift
                    rstart = max( 0, rstart - start )
                    rend = min( length, rend - start )
                    counts[ rstart:rend ] += 1

        else:
            for samfile in bamfiles:
                for read in samfile.fetch( contig, start, end ):
                    nreads += 1
                    rstart = max( 0, read.pos - start )
                    rend = min( length, read.pos - start + read.rlen ) 
                    counts[ rstart:rend ] += 1
                    
        length = end - start            
        avgval = numpy.mean( counts )
        peakval = max(counts)

        # set other peak parameters
        peaks = numpy.array( range(0,length) )[ counts >= peakval ]
        npeaks = len( peaks )
        # peakcenter is median coordinate between peaks
        # such that it is a valid peak in the middle
        peakcenter = start + peaks[npeaks//2] 

        return CounterPeaksResult( length, nreads, avgval, peakval, npeaks, peakcenter )

    def update( self, bed ):
        '''count reads per position.
        
        If offsets are given, shift tags by offset / 2 and extend
        by offset / 2.
        '''

        self.result = self.count( bed, self.bamfiles, self.offsets )
        if self.control_bamfiles:
            self.control = self.count( bed, self.control_bamfiles, self.control_offsets )

    def __str__(self):
        if self.control_bamfiles:
            return "\t".join( map(str, self.result + self.control ) )
        else:
            return "\t".join( map(str, self.result ) )

##------------------------------------------------------------
if __name__ == '__main__':

    parser = optparse.OptionParser( version = "%prog version: $Id: gtf2table.py 2888 2010-04-07 08:48:36Z andreas $", usage = globals()["__doc__"])

    parser.add_option("-g", "--genome-file", dest="genome_file", type="string",
                      help="filename with genome [default=%default]."  )

    parser.add_option("-b", "--bam-file", dest="bam_files", type="string",
                      help="filename with read mapping information. Multiple files can be submitted in a comma-separated list [default=%default]."  )

    parser.add_option( "--control-bam-file", dest="control_bam_files", type="string",
                      help="filename with read mapping information for input/control. Multiple files can be submitted in a comma-separated list [default=%default]."  )

    parser.add_option("-c", "--counter", dest="counters", type="choice", action="append",
                      choices=( "overlap", "peaks", ),
                      help="select counters to apply [default=%default]."  )

    parser.add_option("-o", "--offset", dest="offsets", type="int", action="append",
                      help="tag offsets for tag counting - supply as many as there are bam-files [default=%default]."  )

    parser.add_option( "--control-offset", dest="control_offsets", type="int", action="append",
                      help="control tag offsets for tag counting - supply as many as there are bam-files [default=%default]."  )

    parser.add_option("-a", "--all-fields", dest="all_fields", action = "store_true",
                      help="output all fields in original bed file, by default only the first 4 are output [default=%default]."  )

    parser.add_option("--bed-headers", dest="bed_headers", type="string",
                      help="supply ',' separated list of headers for bed component [default=%default]."  )

    parser.set_defaults(
        genome_file = None,
        counters = [],
        bam_files = None,
        offsets = [],
        control_bam_files = None,
        control_offsets = [],
        all_fields = False,
        bed_headers = "contig,start,end,name",
        )

    (options, args) = E.Start( parser )

    # get files
    if options.genome_file:
        fasta = IndexedFasta.IndexedFasta( options.genome_file )
    else:
        fasta = None

    if options.bam_files:
        bam_files = []
        for bamfile in options.bam_files.split(","):
            bam_files.append( pysam.Samfile(bamfile, "rb" ) )
    else:
        bam_files = None

    if options.control_bam_files:
        control_bam_files = []
        for bamfile in options.control_bam_files.split(","):
            control_bam_files.append( pysam.Samfile(bamfile, "rb" ) )
    else:
        control_bam_files = None

    counters = []

    for c in options.counters:
        if c == "overlap":
            counters.append( CounterOverlap( filename = options.filename_bed,
                                             fasta=fasta,
                                             options = options) )
        elif c == "peaks":
            counters.append( CounterPeaks( bam_files,
                                           options.offsets,
                                           control_bam_files,
                                           options.control_offsets,
                                           options = options ) )

    options.stdout.write( "\t".join( [x.strip() for x in options.bed_headers.split(",") ] ) )
    for counter in counters: 
        options.stdout.write("\t%s" % "\t".join( counter.headers ) )
    options.stdout.write("\n")

    for bed in Bed.iterator(options.stdin):
        if options.all_fields:
            options.stdout.write( str(bed) )
        else:
            options.stdout.write( "\t".join( (bed.contig, 
                                              str(bed.start), 
                                              str(bed.end), 
                                              bed.mFields[0]) ) )
        for counter in counters: 
            counter.update(bed)
            options.stdout.write("\t%s" % str(counter) )
        options.stdout.write("\n")

    E.Stop()
