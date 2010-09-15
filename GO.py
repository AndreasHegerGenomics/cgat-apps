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
GO.py - compute GO enrichment from gene lists
=============================================

:Author: Andreas Heger
:Release: $Id$
:Date: |today|
:Tags: Python

Usage
-----

The script ``GO.py`` will test for enrichment or depletion 
of GO categories within a gene list.

The script uses a hypergeometric test to check if a particular
GO category is enriched in a foreground set with respect
to a background set. Multiple testing is controlled by 
computing an empirical false discovery rate using a sampling 
procedure.

A GO analysis proceeds in three steps:

   1. building gene to GO assignments
   2. create one or more gene lists with foreground and background
   3. run one or more GO analyses for each of the foreground gene lists

Building gene to GO assignments
+++++++++++++++++++++++++++++++

The easiest way to obtain a map from gene identifiers to GO assignments
is to down download GO assignments from the ENSEMBL database. The command
below will download go assignments for the human gene set
and save it in the file :file:`gene2go.data`::

   python GO.py 
      --filename-dump=gene2go.data
      --host=ensembldb.ensembl.org
      --user=anonymous 
      --database=homo_sapiens_core_54_36p
      --port=5306 
   > gene2go.log

In order to use GOslim categories, an additional mapping step needs to be performed.
The sequence of commands is::

    wget http://www.geneontology.org/GO_slims/goslim_goa.obo
    wget http://www.geneontology.org/ontology/gene_ontology.obo
    map2slim -outmap go2goslim.map goslim_goa.obo gene_ontology.obo
    python GO.py 
                --go2goslim 
                --filename-ontology=gene_ontology.obo 
                --slims=go2goslim.map 
                --log=goslim.log 
        < gene2go.data > gene2goslim.data

The first two commands obtain GOslim information. 
`map2slim <http://search.cpan.org/~cmungall/go-perl/scripts/map2slim>`_
is part of Chris Mungall's `go-perl <http://search.cpan.org/~cmungall/go-perl/>`_ module 
and the last command converts the gene-to-GO assignment into gene-to-GOSlim assignments.

The gene-to-GO mapping can be constructed any other way. It is simply 
a table of tab-separated values::

   go_type gene_id go_id   description     evidence
   biol_process    ENSG00000151729 GO:0000002      mitochondrial genome maintenance        NA
   biol_process    ENSG00000025708 GO:0000002      mitochondrial genome maintenance        NA
   biol_process    ENSG00000115204 GO:0000002      mitochondrial genome maintenance        NA
   ...

Building gene lists
+++++++++++++++++++

GO requires a list of genes to test for enrichment. This list is simply
a table of gene identifiers. For example::

   ENSG00000116586
   ENSG00000065809
   ENSG00000164048
   ENSG00000115137
   ENSG00000121210

If no background is given, all genes that have GO assignments will constitute
the background. 

Running the GO analysis
+++++++++++++++++++++++

The command below runs a GO analysis, computing an FDR using 10.000 samples::

    python GO.py 
        --filename-input=gene2go.data
        --genes=foreground
        --background=background 
        --sample=10000
        --fdr 
        --filename-ontology=gene_ontology.obo
        --output-filename-pattern='result/%(go)s.%(section)s' 
   > go.log

The output will be stored in the directory :file:`result` and output files will be
created according to the pattern ``<go>.<section>``. ``<go>`` is one of 
``biol_process``, ``mol_function`` and ``cell_location``.
``<section>`` denotes the file contents. Files output are:

+------------+----------------------------------------------+
|``section`` | contents                                     |
+------------+----------------------------------------------+
|samples     |sampling statistics                           |
+------------+----------------------------------------------+
|overall     |table with full results                       |
+------------+----------------------------------------------+
|results     |table with only the significant results       |
+------------+----------------------------------------------+
|parameters  |input and sampling parameters                 |
+------------+----------------------------------------------+
|fg          |assigments for genes in the foreground set    |
+------------+----------------------------------------------+


Other options
+++++++++++++

The script can accept other ontologies than just GO ontologies. 

Code
----

'''
import os, sys, string, re, getopt, time, optparse, math, tempfile, subprocess, random
import collections

import scipy
import scipy.stats
import scipy.special
import numpy
import Stats
import Database
import Experiment as E
import IOTools

MIN_FLOAT = sys.float_info.min
# The following code was taken from:
#
# http://mail.python.org/pipermail/python-list/2006-January/359797.html
#
#
def lnchoose(n, m):
    nf = scipy.special.gammaln(n + 1)
    mf = scipy.special.gammaln(m + 1)
    nmmnf = scipy.special.gammaln(n - m + 1)
    return nf - (mf + nmmnf)

def hypergeometric_gamma(k, n1, n2, t):
    if t > n1 + n2:
        t = n1 + n2
    if k > n1 or k > t:
        return 0
    elif t > n2 and ((k + n2) < t):
        return 0
    else:
        c1 = lnchoose(n1,k)
        c2 = lnchoose(n2, t - k)
        c3 = lnchoose(n1 + n2 ,t)

    # print "hyperg:", k, n1, n2, t, math.exp(c1 + c2 - c3)
    return max( math.exp(c1 + c2 - c3), MIN_FLOAT )

def hypergeometric_P( k, n0, n1, t):

    GSL_DBL_EPSILON=1e-10

    assert t <= (n0+n1), "t larger than population size"
    assert n0 >= 0, "n0 < 0"
    assert n1 >= 0, "n1 < 0"

    if k >= n0 or k >= t:
        P = 1.0
    elif (k < 0.0):
        P = 0.0
    else:
        P = 0.0;
        mode = int( float(t*n0) / float(n0+n1))
        relerr = 1.0;
        if k < mode :
            i = k;
            relerr = 1.0;
            while(i >= 0 and relerr > GSL_DBL_EPSILON and P < 1.0):
                tmp = hypergeometric_gamma( i, n0, n1, t)
                P += tmp
                relerr = tmp / P
                i -= 1
        else:
            i = mode
            relerr = 1.0;
            while(i <= k and relerr > GSL_DBL_EPSILON and P < 1.0):
                tmp = hypergeometric_gamma( i, n0, n1, t)
                P += tmp
                relerr = tmp / P
                i += 1
            i = mode - 1;
            relerr = 1.0;
            while( i >=0 and relerr > GSL_DBL_EPSILON and P < 1.0):
                tmp = hypergeometric_gamma( i, n0, n1, t)
                P += tmp
                relerr = tmp / P
                i -= 1
    return P

def hypergeometric_Q( k, n0, n1, t):

    GSL_DBL_EPSILON=1e-10
    assert t <= (n0+n1), "t larger than population size"    
    assert n0 >= 0, "n0 < 0"
    assert n1 >= 0, "n1 < 0"
    if k >= n0 or k >= t:
        P = 1.0
    elif (k < 0.0):
        P = 0.0
    else:
        P = 0.0;
        mode = int(float(t * n0) / float(n0+n1))
        relerr = 1.0
        if k < mode :
            i = mode
            relerr = 1.0
            while(i <= t and relerr > GSL_DBL_EPSILON and P < 1.0):
                tmp = hypergeometric_gamma( i, n0, n1, t)
                P += tmp
                relerr = tmp / P
                i += 1
            i = mode - 1
            relerr = 1.0;
            while( i > k and relerr > GSL_DBL_EPSILON and P < 1.0):
                tmp = hypergeometric_gamma( i, n0, n1, t)
                P += tmp
                relerr = tmp / P
                i -= 1
            
        else:
            i = k + 1
            relerr = 1.0
            while(i <= t and relerr > GSL_DBL_EPSILON and P < 1.0):
                tmp = hypergeometric_gamma( i, n0, n1, t)
                P += tmp
                relerr = tmp / P
                i += 1
    return P


class Error(Exception):
    """Base class for exceptions in this module."""
    def __str__(self):
        return str(self.message)
    def _get_message(self, message): return self._message
    def _set_message(self, message): self._message = message
    message = property(_get_message, _set_message)

class InputError(Error):
    """Exception raised for errors in the input.

    Attributes:
        expression -- input expression in which the error occurred
        message -- explanation of the error
    """

    def __init__(self, message):
        self.message = message

class GOEntry:
    
    mNameSpaceMap= {
        'molecular_function' : 'mol_function',
        'cellular_component' : 'cell_location',
        'biological_process' : 'biol_process', 
        }
    
    def __init__(self, default_namespace = "ontology" ):
        self.mNameSpace = default_namespace

    def fromOBO( self, section ):
        """read entry form an OBO formatted file."""

        self.mIsA = []

        for line in section:
            
            data = line[:-1].split(":")
            term = data[0]
            rest = ":".join( data[1:] ).strip()
            if term == "name": self.mName = rest
            elif term == "id": self.mId = rest
            elif term == "namespace": self.mNameSpace = self.mNameSpaceMap.get(rest, rest)
            elif term == "def": self.mDefinition = rest
            elif term == "exact_synonym": self.mSynonym = rest
            elif term == "is_a": self.mIsA.append( rest )
            elif term == "comment": self.mComment = rest
            elif term == "is_obsolete": self.mIsObsolete = True
            
##-------------------------------------------------------------------------------

def readOntology( infile ):
    """read ontology in OBO format from infile.

    returns a dictionary of Ontology entries.
    """
    result = {}

    def iterate_blocks( infile ):

        lines = []
        
        for line in infile:
            if line.strip() == "": 
                if lines: yield lines
                lines = []
                continue

            lines.append( line )

    default_namespace = "ontology"

    for section in iterate_blocks( infile ):

        if section[0].startswith( "[Term]"):
            go = GOEntry( default_namespace = default_namespace )
            go.fromOBO( section )
            result[go.mId] = go
        else:
            for line in section:
                data = line[:-1].split(":")
                if data[0] ==  "default-namespace":
                    default_namespace = data[1].strip()
            
    return result

##-------------------------------------------------------------------------------
class GOSample:
    """store results from sampling.
    """
    def __init__(self, mmin, mmax, mmean, mstddev, mprobovers, mprobunders, counts):

        self.mMin = mmin
        self.mMax = mmax
        self.mMean = mmean
        self.mStddev = mstddev
        self.mProbabilitiesOverRepresentation = mprobovers
        self.mProbabilitiesUnderRepresentation = mprobunders
        self.mCounts = counts

##-------------------------------------------------------------------------------
class GOResult:

    mIsOverRepresented = False
    mGOId = None
    mSampleCountsCategory = 0
    mBackgroundCountsCategory = 0
    mSampleCountsTotal = 0
    mBackgroundCountsTotal = 0
    mProbabilityOverRepresentation = 0
    mProbabilityUnderRepresentation = 0

    def __init__(self, goid):
        self.mGOId = goid

    def UpdateProbabilities( self ):
        """calculate probabilities for given counts.

        """
        if self.mBackgroundCountsTotal == 0:
            return

        # various sanity checs
        assert self.mBackgroundCountsCategory >= self.mSampleCountsCategory, \
            "%s: more counts in foreground (%i) than in the background (%i) - make sure the foreground is part of the background." %\
            (self.mGOId, self.mSampleCountsCategory, self.mBackgroundCountsCategory )

        assert self.mBackgroundCountsTotal >= self.mBackgroundCountsCategory, \
            "%s: background: more counts in category (%i) than in total (%i)." %\
            (self.mGOId, self.mBackgroundCountsCategory, self.mBackgroundCountsTotal)

        assert self.mSampleCountsTotal >= self.mSampleCountsCategory, \
            "%s: forerground: more counts in category (%i) than in total (%i)." %\
            (self.mGOId, self.mSampleCountsCategory, self.mSampleCountsTotal)

        if self.mSampleCountsCategory == 0:
            self.mProbabilityOverRepresentation = 1.0
        else:
            self.mProbabilityOverRepresentation = hypergeometric_Q( self.mSampleCountsCategory - 1,
                                                                    self.mBackgroundCountsCategory,
                                                                    self.mBackgroundCountsTotal - self.mBackgroundCountsCategory,
                                                                    self.mSampleCountsTotal )
        
        self.mProbabilityUnderRepresentation = hypergeometric_P( self.mSampleCountsCategory,
                                                                 self.mBackgroundCountsCategory,
                                                                 self.mBackgroundCountsTotal - self.mBackgroundCountsCategory,
                                                                 self.mSampleCountsTotal )
        

        if self.mSampleCountsTotal == 0 or self.mBackgroundCountsCategory == 0:
            self.mRatio = "na"
        else:
            self.mRatio = float(self.mSampleCountsCategory) * self.mBackgroundCountsTotal / self.mSampleCountsTotal / self.mBackgroundCountsCategory 

    def __str__(self):
        """return string representation."""        
        return "%i\t%i\t%s\t%i\t%i\t%s\t%s\t%6.4e\t%6.4e\t%6.4e" % (self.mSampleCountsCategory,
                                                                    self.mSampleCountsTotal,
                                                                    IOTools.prettyPercent( self.mSampleCountsCategory, self.mSampleCountsTotal ),
                                                                    self.mBackgroundCountsCategory,
                                                                    self.mBackgroundCountsTotal,
                                                                    IOTools.prettyPercent( self.mBackgroundCountsCategory, self.mBackgroundCountsTotal ),
                                                                    IOTools.prettyFloat( self.mRatio ),
                                                                    min(self.mProbabilityOverRepresentation,self.mProbabilityUnderRepresentation),
                                                                    self.mProbabilityOverRepresentation,
                                                                    self.mProbabilityUnderRepresentation )

##-------------------------------------------------------------------------------                                           
class GOResults:

    def __init__(self):
        self.mResults = {}
        self.mNumGenes = 0
        self.mBackgroundCountsTotal = 0
        self.mSampleCountsTotal = 0

    def __str__(self):
        """return string representation."""
        lines = []
        lines.append( "\t".join(map(str, (self.mNumGenes, self.mBackgroundCountsTotal, self.mSampleCountsTotal))))
        for k, v in self.mResults.items():
            lines.append("%s\t%s" % (k, str(v)))
        return "\n".join(lines)

##-------------------------------------------------------------------------------
class GOInfo:
    mGOId = None
    mGOType = None
    mDescription = None
    
    def __init__( self,
                  goid = None,
                  go_type = None,
                  description = None ):

        self.mDescription = description
        self.mGOId = goid
        self.mGOType = go_type

    def __str__(self):
        if self.mGOId == None:
            return "\t".join(map(str, ("", "", "")))
        else:
            return "\t".join(map(str, (self.mGOId, self.mGOType, self.mDescription)))
    

##-------------------------------------------------------------------------------
class GOMatch(GOInfo):
    mEvidence = None
    
    def __init__( self,
                  goid = None,
                  go_type = None,
                  description = None,
                  evidence = None ):

        GOInfo.__init__(self, goid, go_type, description)
        self.mEvidence = evidence

    def __str__(self):
        return "\t".join(map(str, (self.mGOId, self.mGOType, self.mDescription, self.mEvidence)))

##---------------------------------------------------------------------
def FilterByGOIds(  gene2go, go2info ):

    """
    filter gene_id to go_id lookup by a list of go_ids

    returns a new gene2go mapping.
    
    used to restrict GO terms to GO_slim and remove alternates

    gene2go 				# starting set, map of genes to go terms
    go2info				# alt ids are repeats of superceding ids
    """

    filtered_gene2go = {}

    for gene_id in gene2go.keys():
        new_go = set()
        for go in gene2go[gene_id]:
            if go.mGOId in go2info:
                new_go.add( go )

        if new_go:
            filtered_gene2go[gene_id] = list(new_go)

    return filtered_gene2go

##---------------------------------------------------------------------
def MapGO2Slims(  gene2go, go2slim, ontology = None ):
    """filter gene2go lookup by a list of go_ids in go2slim.

    gene2go: map of genes to go terms
    go2slim: map of go categories to goslim go categories   

    If ontology is given, missing descriptions of go entries
    are added from the ontology.

    returns a new gene2go mapping.
    """

    ## build map of go identifiers to go info
    map_go2info = {}
    if ontology:
        for go in ontology.values():
            map_go2info[go.mId] = GOInfo( goid=go.mId, 
                                          go_type = go.mNameSpace,
                                          description = go.mName )
    else:
        for gene_id, gos in gene2go.items():
            for go in gos:
                map_go2info[go.mGOId] = go

    filtered_gene2go = {}

    for gene_id, gos in gene2go.items():    
        new_go = set()
        for go in gos:
            if go.mGOId in go2slim:
                for gg in go2slim[go.mGOId]:
                    if gg in map_go2info:
                        new_go.add( map_go2info[gg] )
                    else:
                        raise IndexError, "description for mapped go term not present: %s -> %s" % (go.mGOId, gg)
        if new_go:
            filtered_gene2go[gene_id] = list(new_go)

    return filtered_gene2go

##------------------------------------------------------------------------------
def GetGOSlims( infile ):
    """
    returns a map of go identifiers to slim categories

    Input is the output of Chris Mungal's map2slim.pl.
    """

    go2go = {}
    for line in infile:
        if line[:len("part_of")] == "part_of": continue

        mapped, parents = line.split("//")
        go, goslims = mapped.split("=>")
        goslims = goslims.split(" ")
        if len(goslims) == 0 : continue

        go2go[go.strip()] = filter( lambda x: len(x), map(lambda x: x.strip(), goslims))
        
    return go2go

##------------------------------------------------------------------------------
def GetGOFrequencies( gene2go, genes ):
    """count number of each go category in gene list.

    return a tuple containing:
    * the total number of GO categories found.
    * dictionary of counts per GO category
    * dictionary of genes found with GO categories
    """
    counts = {}
    total = 0
    found_genes = {}
    
    for gene_id in genes:
        
        if gene_id not in gene2go: continue

        found_genes[gene_id] = 1
        for go in gene2go[gene_id]:
            if go.mGOId not in counts: counts[go.mGOId] = 0
            counts[go.mGOId] += 1
            total += 1

    return total, counts, found_genes

##------------------------------------------------------------------------------
def AnalyseGO( gene2go,
               genes,
               genes_background = (),
               do_probabilities = True):
    """analyse go ids.

    goids: list of goids to analyse
    genes: sample set of genes
    genes_background: background set of genes (default: all)
    """
    
    if genes_background == ():
        genes_background = gene2go.keys()

    result = GOResults()
    
    ## get background frequencies
    (background_counts_total, background_counts, background_genes) = \
        GetGOFrequencies( gene2go, 
                          genes_background )
    
    result.mBackgroundCountsTotal = background_counts_total
    result.mBackgroundNumCategories = len(background_counts)
    result.mBackgroundGenes = background_genes

    ## get sample frequencies
    (sample_counts_total, sample_counts, sample_genes) = \
                          GetGOFrequencies( gene2go, 
                                            genes )

    result.mNumGenes = len(genes)

    result.mSampleCountsTotal = sample_counts_total
    result.mSampleNumCategories = len(sample_counts)
    result.mSampleGenes = sample_genes
    
    # test for over or underrepresented categories in the slims
    # report results for all go categories in the background
    # so that also categories completely absent in the foreground (sample)
    # are considered.
    for go_id in background_counts.keys():
        
        result_go = GOResult(go_id)

        # use gene counts
        result_go.mSampleCountsCategory = sample_counts.get( go_id, 0 )
        result_go.mSampleCountsTotal = len(sample_genes)
        result_go.mBackgroundCountsTotal = len(background_genes)
        result_go.mBackgroundCountsCategory = background_counts[go_id]

        E.debug( "processing %s: genes in foreground=%i, genes in backgound=%i, sample_counts=%i, background_counts=%i" % \
                     ( go_id,
                       len(sample_genes),
                       len(background_genes),
                       sample_counts.get(go_id,0),
                       background_counts.get(go_id,0),
                       )
                 )

        if do_probabilities:
            try:
                result_go.UpdateProbabilities()
            except AssertionError, msg:
                print msg
                print "# error while calculating probabilities for %s" % go_id
                print "# genes in sample", sample_genes
                print "# counts in sample: %i out of %i total" % ( result_go.mSampleCountsCategory, result_go.mSampleCountsTotal)
                print "# counts in background %i out of %i total" % (result_go.mBackgroundCountsCategory, result_go.mBackgroundCountsTotal)
                for x in sample_genes.keys():
                    for y in gene2go[x]:
                        print x, str(y)

                sys.exit(0)
        
        result.mResults[go_id] = result_go
        
    return result

##--------------------------------------------------------------------------------
def GetGOStatement( go_type, database, species ):
    """build statement to get GO assignments for genes from ENSEMBL."""
    
    if database in ("ensembl_mart_27_1", ):
        statement = """SELECT DISTINCTROW
        gene_stable_id, glook_%s_id, description, olook_evidence_code
        FROM %s.%s_gene_ensembl__go_%s__look
        WHERE glook_%s_id IS NOT NULL
        GROUP BY gene_stable_id, glook_%s_id, description
        ORDER BY gene_stable_id
        """ % (go_type,
               database, species, go_type,
               go_type, go_type)
        
    elif database in ("ensembl_mart_31", "ensembl_mart_37", "ensembl_mart_41" ):
        statement = """SELECT DISTINCTROW
        gene_stable_id, glook_%s_id, description, olook_evidence_code
        FROM %s.%s_go_%s__go_%s__main
        WHERE glook_%s_id IS NOT NULL
        GROUP BY gene_stable_id, glook_%s_id, description
        ORDER BY gene_stable_id
        """ % (go_type,
               database, species, go_type, go_type,
               go_type, go_type)

    elif re.search( "core", database):

        if go_type == "biol_process":
            go_type = "biological_process"
        elif go_type == "mol_function":
            go_type = "molecular_function"
        elif go_type == "cell_location":
            go_type = "cellular_component"
        else:
            raise "unknown go_type %s" % go_type

        x = re.search("(\d+)", database)
        if not x:
            raise "can't find version number in database %s" % database

        version = int(x.groups()[0])
        if version <= 54:
            go_database = "ensembl_go_%s" % version
            go_field = "acc"
            statement = """SELECT DISTINCTROW
        g.stable_id, xref.dbprimary_acc, go.name, 'NA'
        FROM gene, transcript, translation, 
        gene_stable_id as g, object_xref as o, xref,
        %(go_database)s.term AS go
        WHERE gene.gene_id = transcript.gene_id
        AND transcript.transcript_id = translation.transcript_id
        AND g.gene_id = gene.gene_id
        AND translation.translation_id = o.ensembl_id
        AND xref.xref_id = o.xref_id
        AND go.%(go_field)s = xref.dbprimary_acc
        AND go.term_type = '%(go_type)s'
        AND xref.external_db_id = 1000
        """ % locals()

        else:
            go_database = "ensembl_ontology_%s" % version
            go_field = "accession"

            statement = """SELECT DISTINCTROW
        g.stable_id, xref.dbprimary_acc, go.name, 'NA'
        FROM gene, transcript, translation, 
        gene_stable_id as g, object_xref as o, xref,
        %(go_database)s.term AS go,
        %(go_database)s.ontology AS ontology
        WHERE gene.gene_id = transcript.gene_id
        AND transcript.transcript_id = translation.transcript_id
        AND g.gene_id = gene.gene_id
        AND translation.translation_id = o.ensembl_id
        AND xref.xref_id = o.xref_id
        AND go.%(go_field)s = xref.dbprimary_acc
        AND go.ontology_id = ontology.ontology_id 
        AND ontology.namespace = '%(go_type)s'
        AND xref.external_db_id = 1000
        """ % locals()

    else:
        raise "unknown ensmart version %s" % database

    return statement

##--------------------------------------------------------------------------------
def ReadGene2GOFromDatabase( dbhandle, go_type, database, species ):
    """read go assignments from ensembl database.

    returns a dictionary of lists.
    (one to many mapping of genes to GO categories)
    and a dictionary of go-term to go information

    Note: assumes that external_db_id for GO is 1000
    """

    statement = GetGOStatement( go_type, database, species )
    result = dbhandle.Execute(statement).fetchall()

    gene2go = {}
    go2info = {}
    for gene_id, goid, description, evidence in result:
        gm = GOMatch( goid, go_type, description, evidence )
        gi = GOInfo( goid, go_type, description )
        if gene_id not in gene2go: gene2go[gene_id] = []
        gene2go[gene_id].append(gm)
        go2info[goid] = gi
    
    return gene2go, go2info

##---------------------------------------------------------------------------
def DumpGOFromDatabase( outfile, 
                        dbhandle, 
                        options ):

    """read go assignments from database.

    and dump them into a flatfile.
    (one to many mapping of genes to GO categories)
    and a dictionary of go-term to go information
    """

    if options.loglevel >= 1:    
        options.stdlog.write("# category\ttotal\tgenes\tcategories\n" )

    all_genes = collections.defaultdict( int )
    all_categories = collections.defaultdict( int )
    all_ntotal = 0

    outfile.write("go_type\tgene_id\tgo_id\tdescription\tevidence\n" )

    for go_type in options.ontology:

        genes = collections.defaultdict( int )
        categories = collections.defaultdict( int )
        ntotal = 0

        statement = GetGOStatement( go_type, options.database, options.species )
        
        results = dbhandle.Execute(statement).fetchall()

        for result in results:
            outfile.write( "\t".join(map( str, (go_type,) + result))+ "\n")
            gene_id, goid, description, evidence = result
            genes[gene_id] += 1
            categories[goid] += 1
            ntotal += 1
            all_genes[gene_id] += 1
            all_categories[goid] += 1
            all_ntotal += 1
            
        if options.loglevel >= 1:    
            options.stdlog.write( "# %s\t%i\t%i\t%i\n" % (go_type, ntotal,
                                                          len(genes),
                                                          len(categories) ) )


    if options.loglevel >= 1:    
        options.stdlog.write( "# %s\t%i\t%i\t%i\n" % ("all", 
                                                      all_ntotal,
                                                      len(all_genes),
                                                      len(all_categories) ) )

    return 

##---------------------------------------------------------------------------
def ReadGene2GOFromFile( infile ):
    """reads GO mappings for all go_types from a
    file.

    returns two maps: gene2go maps genes to go categories
    and go2info maps go categories to information.
    """

    gene2gos = {}
    go2infos = {}
    for line in infile:
        if line[0] == "#": continue
        go_type, gene_id, goid, description, evidence = line[:-1].split("\t")
        if go_type == "go_type": continue

        gm = GOMatch( goid, go_type, description, evidence )
        gi = GOInfo( goid, go_type, description )
        if go_type not in gene2gos:
            gene2gos[go_type] = {}
            go2infos[go_type] = {}

        gene2go = gene2gos[go_type]
        go2info = go2infos[go_type]
        
        if gene_id not in gene2go: gene2go[gene_id] = []
        gene2go[gene_id].append(gm)
        go2info[goid] = gi
    
    return gene2gos, go2infos

##---------------------------------------------------------------------------
def CountGO( gene2go ):
    """count number of genes and go categories in mapping."""

    cats = {}
    nmaps = 0
    for k,vv in gene2go.items():
        for v in vv:
            nmaps += 1                    
            cats[v.mGOId] = 1
            
    return len(gene2go), len(cats), nmaps

##---------------------------------------------------------------------------
def countGOs( gene2gos ):
    """return map of number of genes and go categories in mapping."""
    genes, goids = collections.defaultdict( int ), collections.defaultdict( int )

    for cat, gene2go in gene2gos.iteritems():
        for gene_id, vv in gene2go.iteritems():
            genes[gene_id] += 1
            for v in vv:
                goids[v.mGOId] += 1
    return genes, goids

##---------------------------------------------------------------------------
def ReadGeneList( filename_genes, options ):
    
    """read gene list from filename."""

    if filename_genes == "-":
        infile = sys.stdin
    else:
        infile = open(filename_genes,"r")

    genes = map( lambda x: x[:-1].split("\t")[0], filter( lambda x: x[0] != "#", infile.readlines()))
    infile.close()
    E.info( "read %i genes from %s" % (len(genes), filename_genes))

    ## apply transformation
    if options.gene_pattern:
        rx = re.compile(options.gene_pattern)
        genes = map( lambda x: rx.search( x ).groups()[0], genes )
            
    #############################################################
    ## make non-redundant
    xx = {}
    for x in genes: xx[x] = 1
    genes = xx.keys()

    if filename_genes != "-":
        infile.close()

    E.info("after filtering: %i nonredundant genes." % (len(genes)))

    return genes

##---------------------------------------------------------------------------
def GetCode( v ):
    """return a code for over/underrepresentation."""

    if v.mRatio > 1.0:
        code = "+"
    elif v.mRatio < 1.0:
        code = "-"
    else:
        code = "?"
    return code

##---------------------------------------------------------------------------    
def convertGo2Goslim( options ):
    """read gene list with GO assignments and convert to GO slim categories."""
    
    E.info( "reading GO assignments from stdin" )
    gene2gos, go2infos = ReadGene2GOFromFile( options.stdin )
    input_genes, input_goids = countGOs( gene2gos )

    #############################################################
    ## read GO ontology from file
    assert options.filename_ontology, "please supply a GO ontology"
    E.info( "reading ontology from %s" % (options.filename_ontology) )
        
    infile = open(options.filename_ontology)
    ontology = readOntology( infile )
    infile.close()
        
    go2infos = collections.defaultdict( dict )
    # substitute go2infos
    for go in ontology.values():
        go2infos[go.mNameSpace][go.mId] = GOInfo( go.mId,
                                                  go_type = go.mNameSpace,
                                                  description = go.mName )

    E.info( "reading GO assignments from %s" % options.filename_slims)
    go_slims = GetGOSlims( open(options.filename_slims, "r") )

    if options.loglevel >=1:
        v = set()
        for x in go_slims.values():
            for xx in x: v.add(xx)
        E.info( "read go slims from %s: go=%i, slim=%i" %\
                                  ( options.filename_slims,
                                    len(go_slims), 
                                    len( v ) ))
                
    output_goids, output_genes = set(), set()
    noutput = 0
    options.stdout.write( "\t".join( ("go_type", "gene_id", "go_id", "description", "evidence" ) ) + "\n" )
    for category, gene2go in gene2gos.iteritems():
        gene2go = MapGO2Slims( gene2go, go_slims, ontology )
        for gene_id, values in gene2go.iteritems():
            output_genes.add( gene_id )
            for go in values:
                output_goids.add( go.mGOId)
                options.stdout.write( "%s\t%s\t%s\t%s\t%s\n" % \
                                          ( go.mGOType,
                                            gene_id,
                                            go.mGOId,
                                            go.mDescription,
                                            "NA", ) )
                noutput += 1

    E.info( "ninput_genes=%i, ninput_goids=%i, noutput_gene=%i, noutput_goids=%i, noutput=%i" % \
                         (len(input_genes), len(input_goids),
                          len(output_genes), len(output_goids),
                          noutput) )

def outputResults( outfile, pairs, go2info,
                   fdrs = None, samples = None ):
    '''output GO results to outfile.'''

    headers = ["code",
               "scount", "stotal", "spercent", 
               "bcount", "btotal", "bpercent", 
               "ratio",
               "pvalue", "pover", "punder", 
               "goid", "category", "description"]

    if fdrs:
        headers += ["fdr"]

    if samples:
        headers += ["min", "max", "zscore", "mpover", "mpunder", 
                    "nfdr_expected",
                    "CI95lower", "CI95upper" ]

    outfile.write("\t".join(headers) + "\n" )

    nselected = 0

    for k, v in pairs:

        code = GetCode( v )

        n = go2info.get( k, GOInfo() )

        outfile.write("%s\t%s\t%s" % (code, str(v), n))
        
        if options.fdr:
            fdr = fdrs[k][0]
            outfile.write( "\t%f" % fdr )

        if options.sample:

            if k in samples:
                s = samples[k]
            else:
                outfile.write("\n")

            ## calculate values for z-score
            if s.mStddev > 0:
                zscore = abs(float(v.mSampleCountsCategory) - s.mMean) / s.mStddev
            else:
                zscore = 0.0

            # the number of expected false positives is the current FDR times the
            # number of hypothesis selected.
            nexpected = nselected * fdr

            outfile.write("\t%i\t%i\t%f\t%5.2e\t%5.2e\t%6.4f\t%6.4f\t%6.4f" %\
                          (s.mMin,
                           s.mMax,
                           zscore,
                           min(s.mProbabilitiesOverRepresentation),
                           min(s.mProbabilitiesUnderRepresentation),
                           scipy.mean( s.mCounts ),
                           scipy.stats.scoreatpercentile( s.mCounts, 5 ),
                           scipy.stats.scoreatpercentile( s.mCounts, 95 ),
                           ) )

        outfile.write("\n")


def getSamples( gene2go, genes, background, options ):

    sample_size = options.sample
    # List of all minimum probabilities in simulation
    simulation_min_pvalues = []
    E.info( "sampling: calculating %i samples: " % (sample_size))

    counts = {}
    prob_overs = {}
    prob_unders = {}

    samples = {}

    options.stdlog.write("# ")
    options.stdlog.flush()

    for x in range(sample_size):

        if options.loglevel >= 1:
            options.stdlog.write( "." )
            options.stdlog.flush()

        ## get shuffled array of genes from background
        sample_genes = random.sample( background, len(genes) )

        go_results = AnalyseGO( gene2go , sample_genes, background )

        pairs = go_results.mResults.items()

        for k, v in pairs:
            if k not in counts:
                counts[k] = []
                prob_overs[k] = []
                prob_unders[k] = []

            counts[k].append( v.mSampleCountsCategory )
            prob_overs[k].append( v.mProbabilityOverRepresentation )
            prob_unders[k].append( v.mProbabilityUnderRepresentation )                    

            simulation_min_pvalues.append( min( v.mProbabilityUnderRepresentation,
                                                v.mProbabilityOverRepresentation ) )


    if options.loglevel >= 1:
        sys.stdout.write("\n")
        sys.stdout.flush()

    E.info("sampling: sorting %i P-Values" % len(simulation_min_pvalues) )

    simulation_min_pvalues.sort()
    simulation_min_pvalues = numpy.array(simulation_min_pvalues)

    samples = {}


    if options.output_filename_pattern:
        filename = options.output_filename_pattern % { 'go': ontology, 'section': "samples" }
        E.info( "sampling results go to %s" % filename )
        outfile = IOTools.openFile( filename, "w", create_dir = True )
    else:
        outfile = sys.stdout

    outfile.write( "\t".join( ("goid", "min", "max", "mean", "median", "stddev", 
                               "CI95lower", "CI95upper",
                               "pover", "punder", "goid",
                               "category", "description") ) + "\n" )
    for k in counts.keys():

        c = counts[k]

        prob_overs[k].sort()
        prob_unders[k].sort()

        s = GOSample(min(c),
                     max(c),
                     scipy.mean(c),
                     numpy.std(c),
                     numpy.array(prob_overs[k]),
                     numpy.array(prob_unders[k]),
                     counts[k] )

        samples[k] = s

        if k in go2info:
            n = go2info[k]
        else:
            n = "?"

        outfile.write( "%s\t%i\t%i\t%f\t%f\t%f\t%f\t%f\t%f\t%f\t%s\n" %\
                       (k,
                        min(c),
                        max(c),
                        scipy.mean(c),
                        scipy.median(c),
                        numpy.std(c),
                        scipy.stats.scoreatpercentile( c, 5 ),
                        scipy.stats.scoreatpercentile( c, 95 ),
                        min(prob_overs[k]),
                        min(prob_unders[k]),
                        n ))
    if options.output_filename_pattern:
        outfile.close()

    return samples, simulation_min_pvalues

##---------------------------------------------------------------------------    
if __name__ == "__main__":

    parser = optparse.OptionParser( version = "%prog version: $Id: GO.py 2883 2010-04-07 08:46:22Z andreas $", usage = globals()["__doc__"])

    dbhandle = Database.Database()
    
    parser.add_option("-s", "--species", dest="species", type="string",
                      help="species to use [default=%default]." )

    parser.add_option("-i", "--slims", dest="filename_slims", type="string",
                      help="filename with GO SLIM categories [default=%default].")

    parser.add_option( "-g", "--genes", dest="filename_genes", type="string",
                       help="filename with genes to analyse [default=%default]." )

    parser.add_option( "-b", "--background", dest="filename_background", type="string",
                       help="filename with background genes to analyse [default=%default]." )

    parser.add_option( "-o", "--sort-order", dest="sort_order", type="choice",
                       choices=("fdr", "pover", "ratio" ),
                       help="output sort order [default=%default]." )

    parser.add_option( "--ontology", dest="ontology", type="choice", action="append",
                       choices=("biol_process","cell_location","mol_function", "mgi" ),
                       help="go ontologies to analyze. Ontologies are tested separately."
                       " [default=%default]." )

    parser.add_option( "-t", "--threshold", dest="threshold", type="float",
                       help="significance threshold [>1.0 = all ]. If --fdr is set, this refers to the fdr, otherwise it is a cutoff for p-values." )

    parser.add_option ("--filename-dump", dest="filename_dump", type="string",
                       help="dump GO category assignments into a flatfile [default=%default]." )

    parser.add_option ("--filename-ontology", dest="filename_ontology", type="string",
                       help="filename with ontology in OBO format [default=%default]." )

    parser.add_option ( "--filename-input", dest="filename_input", type="string",
                       help="read GO category assignments from a flatfile [default=%default]." )

    parser.add_option ( "--sample", dest="sample", type="int",
                       help="do sampling (with # samples) [default=%default]." )

    parser.add_option ( "--filename-output-pattern", dest = "output_filename_pattern", type="string",
                        help="pattern with output filename pattern (should contain: %(go)s and %(section)s ) [default=%default]")

    parser.add_option ( "--output-filename-pattern", dest = "output_filename_pattern", type="string",
                        help="pattern with output filename pattern (should contain: %(go)s and %(section)s ) [default=%default]")
    
    parser.add_option ( "--fdr", dest="fdr", action="store_true",
                       help="calculate and filter by FDR [default=%default]." )

    parser.add_option ( "--go2goslim", dest="go2goslim", action="store_true",
                       help="convert go assignments in STDIN to goslim assignments and write to STDOUT [default=%default]." )

    parser.add_option ( "--gene-pattern", dest = "gene_pattern", type="string",
                        help="pattern to transform identifiers to GO gene names [default=%default].")

    parser.add_option( "--filename-map-slims", dest="filename_map_slims", type="string",
                       help="write mapping between GO categories and GOSlims [default=%default].")

    parser.add_option( "--get-genes", dest="get_genes", type="string",
                       help="list all genes in the with a certain GOID [default=%default]." )

    parser.add_option( "--strict", dest="strict", action="store_true",
                       help="require all genes in foreground to be part of background. "
                       "If not set, genes in foreground will be added to the background [default=%default]." )
    
    parser.set_defaults( species = None,
                         filename_genes = "-",
                         filename_background = None,
                         filename_slims = None,
                         ontology = [],
                         filename_dump = None,
                         sample = 0,
                         fdr = False,
                         output_filename_pattern = None,
                         threshold = 0.05,
                         filename_map_slims = None,
                         gene_pattern = None,
                         sort_order = "ratio",
                         get_genes = None,
                         strict = False )

    (options, args) = E.Start( parser, add_mysql_options = True )

    if options.go2goslim:
        convertGo2Goslim( options )
        E.Stop()
        sys.exit(0)

    if options.fdr and options.sample == 0:
        E.warn( "fdr will be computed without sampling" )

    #############################################################
    ## dump GO
    if options.filename_dump:
        # set default orthologies to GO
        if not options.ontology:
            options.ontology = ["biol_process", "mol_function", "cell_location"] 

        E.info( "dumping GO categories to %s" % (options.filename_dump) )

        dbhandle.Connect( options )
            
        outfile = IOTools.openFile( options.filename_dump, "w", create_dir = True )
        DumpGOFromDatabase( outfile,
                            dbhandle,
                            options )
        outfile.close()
        E.Stop()
        sys.exit(0)

    #############################################################
    ## read GO categories from file
    if options.filename_input:
        E.info( "reading association of categories and genes from %s" % (options.filename_input) )
        infile = open(options.filename_input)
        gene2gos, go2infos = ReadGene2GOFromFile( infile )
        infile.close()

    #############################################################
    ## read GO ontology from file
    if options.filename_ontology:
        E.info( "reading ontology from %s" % (options.filename_ontology) )
        
        infile = open(options.filename_ontology)
        ontology = readOntology( infile )
        infile.close()
        
        go2infos = collections.defaultdict( dict )
        ## substitute go2infos
        for go in ontology.values():
            go2infos[go.mNameSpace][go.mId] = GOInfo( go.mId,
                                                      go_type = go.mNameSpace,
                                                      description = go.mName )

    #############################################################
    ## get foreground gene list
    genes = ReadGeneList( options.filename_genes, options )
        
    #############################################################
    ## get background
    if options.filename_background:
        input_background = ReadGeneList( options.filename_background, options )

        E.info( "read %i genes for background" % len(input_background) )
    else:
        input_background = None

    #############################################################
    ## sort out which ontologies to test
    if not options.ontology: 
        if options.filename_input:
            options.ontology = gene2gos.keys()

    #############################################################
    ## get go categories for genes
    for ontology in options.ontology:

        #############################################################
        ## get/read association of GO categories to genes
        if options.filename_input:
            gene2go, go2info = gene2gos[ontology], go2infos[ontology]
        else:
            if options.loglevel >= 1:
                options.stdlog.write( "# reading data from database ..." )
                sys.stdout.flush()

            dbhandle.Connect( options )
            gene2go, go2info = ReadGene2GOFromDatabase( dbhandle,
                                                        ontology,
                                                        options.database, options.species )

            if options.loglevel >= 1:
                options.stdlog.write( "finished.\n" )
                sys.stdout.flush()

        if len(go2info) == 0:
            E.warn( "could not find information for terms - could be mismatch between ontologies")

        ngenes, ncategories, nmaps = CountGO( gene2go )        
        E.info( "read GO assignments: %i genes mapped to %i categories (%i maps)" % (ngenes, ncategories, nmaps) )

        ##################################################################
        ##################################################################
        ##################################################################
        ## build background - reconcile with foreground
        ##################################################################
        if input_background == None:
            background = tuple(gene2go.keys())
        else:
            background = input_background 
            
        missing = set(genes).difference( set(background))

        if options.strict:
            assert len(missing) == 0, \
                "%i genes in foreground but not in background: %s" % (len(missing), str(missing))
        else:
            if len(missing) != 0:
                E.warn( "%i genes in foreground that are not in background - added to background of %i" %\
                            (len(missing), len(background)) )
            background.extend( missing )

        E.info( "(unfiltered) foreground=%i, background=%i" % (len(genes), len(background)))

        #############################################################
        ## sanity check:            
        ## are all of the foreground genes in the dataset
        ## missing = set(genes).difference( set(gene2go.keys()) )
        ## assert len(missing) == 0, "%i genes in foreground set without GO annotation: %s" % (len(missing), str(missing))

        #############################################################            
        ## read GO slims and map GO categories to GO slim categories
        if options.filename_slims:
            go_slims = GetGOSlims( open(options.filename_slims, "r") )
            
            if options.loglevel >=1:
                v = set()
                for x in go_slims.values():
                    for xx in x: v.add(xx)
                options.stdlog.write( "# read go slims from %s: go=%i, slim=%i\n" %\
                                          ( options.filename_slims,
                                            len(go_slims), 
                                            len( v ) ))

                                       

            if options.filename_map_slims:
                if options.filename_map_slims == "-":
                    outfile = options.stdout
                else:
                    outfile=IOTools.openFile(options.filename_map_slims, "w" )

                outfile.write( "GO\tGOSlim\n" )
                for go, go_slim in go_slims.items():
                    outfile.write("%s\t%s\n" % (go, go_slim))

                if outfile != options.stdout:
                    outfile.close()

            gene2go = MapGO2Slims( gene2go, go_slims, ontology )

            if options.loglevel >=1:
                ngenes, ncategories, nmaps = CountGO( gene2go )
                options.stdlog.write( "# after go slim filtering: %i genes mapped to %i categories (%i maps)\n" % (ngenes, ncategories, nmaps) )

        #############################################################
        ## Just dump out the gene list
        if options.get_genes:
            fg, bg, ng = [], [], []

            for gene, vv in gene2go.items():
                for v in vv:
                    if v.mGOId == options.get_genes:
                        if gene in genes:
                            fg.append( gene )
                        elif gene in background:
                            bg.append( gene )
                        else:
                            ng.append( gene )

            ## skip to next GO class
            if not (bg or ng): continue

            options.stdout.write( "# genes in GO category %s\n" % options.get_genes )
            options.stdout.write( "gene\tset\n" )
            for x in fg: options.stdout.write("%s\t%s\n" % ("fg", x))
            for x in bg: options.stdout.write("%s\t%s\n" % ("bg", x))           
            for x in ng: options.stdout.write("%s\t%s\n" % ("ng", x))                       

            E.info( "nfg=%i, nbg=%i, nng=%i" % (len(fg), len(bg), len(ng) ))
                
            E.Stop()
            sys.exit(0)
                  
        #############################################################
        ## do the analysis
        go_results = AnalyseGO( gene2go, genes, background )

        if len(go_results.mSampleGenes) == 0:
            E.warn( "no genes with GO categories - analysis aborted" )
            E.Stop()
            sys.exit(0)

        pairs = go_results.mResults.items()

        #############################################################################
        ## sampling
        ## for each GO-category:
        ##      get maximum and minimum counts in x samples -> calculate minimum/maximum significance
        ##      get average and stdev counts in x samples -> calculate z-scores for test set
        samples, simulation_min_pvalues = getSamples( gene2go, genes, background, options )

        #############################################################
        ## calculate fdr for each hypothesis
        fdrs = {}

        if options.fdr:

            E.info( "calculating the FDRs" )
                
            observed_min_pvalues = [ min(x[1].mProbabilityOverRepresentation,
                                         x[1].mProbabilityUnderRepresentation) for x in pairs ]

            if options.sample == 0:
                # compute fdr via Storey's method
                fdr_data = Stats.doFDR( observed_min_pvalues )
                
                for pair, qvalue in zip( pairs, fdr_data.mQValues ):
                    fdrs[pair[0]] = (qvalue, 1.0, 1.0)

            else:
                # compute P-values from sampling
                observed_min_pvalues.sort()
                observed_min_pvalues = numpy.array( observed_min_pvalues )

                sample_size = options.sample

                for k, v in pairs:

                    if k in samples:
                        s = samples[k]
                    else:
                        raise KeyError("category %s not in samples" % k)

                    ## calculate values for z-score
                    if s.mStddev > 0:
                        zscore = abs(float(v.mSampleCountsCategory) - s.mMean) / s.mStddev
                    else:
                        zscore = 0.0

                    #############################################################
                    # FDR:
                    # For each p-Value p at node n:
                    #   a = average number of nodes in each simulation run with P-Value < p
                    #           this can be obtained from the array of all p-values and all nodes
                    #           simply divided by the number of samples.
                    #      aka: expfpos=experimental false positive rate
                    #   b = number of nodes in observed data, that have a P-Value of less than p.
                    #      aka: pos=positives in observed data
                    #   fdr = a/b
                    pvalue = min(v.mProbabilityOverRepresentation,
                                 v.mProbabilityUnderRepresentation)

                    # calculate values for FDR: 
                    # nfdr = number of entries with P-Value better than node.
                    a = 0
                    while a < len(simulation_min_pvalues) and \
                              simulation_min_pvalues[a] < pvalue:
                        a += 1
                    a = float(a) / float(sample_size)
                    b = 0
                    while b < len(observed_min_pvalues) and \
                            observed_min_pvalues[b] < pvalue:
                        b += 1

                    if b > 0:
                        fdr = min(1.0, float(a) / float(b))
                    else:
                        fdr = 1.0

                    fdrs[k] = (fdr, a, b)

        if options.sort_order == "fdr":
            pairs.sort( lambda x, y: cmp(fdrs[x[0]], fdrs[y[0]] ) )           
        elif options.sort_order == "ratio":
            pairs.sort( lambda x, y: cmp(x[1].mRatio, y[1].mRatio))
        elif options.sort_order == "pover":
            pairs.sort( lambda x, y: cmp(x[1].mProbabilityOverRepresentation, y[1].mProbabilityOverRepresentation))

        #############################################################
        # output filtered results
        filtered_pairs = []

        for k, v in pairs:
            
            is_ok = False

            pvalue = min(v.mProbabilityOverRepresentation, v.mProbabilityUnderRepresentation) 

            if options.fdr:
                (fdr, expfpos, pos) = fdrs[k]
                if fdr < options.threshold: is_ok = True
            else:
                if pvalue < options.threshold: is_ok = True
                
            if is_ok: filtered_pairs.append( (k,v) )

        nselected = len(filtered_pairs)

        if options.output_filename_pattern:
            filename = options.output_filename_pattern % { 'go': ontology, 'section': "results" }
            E.info( "results go to %s" % filename)
            outfile = IOTools.openFile(filename, "w", create_dir = True)
        else:
            outfile = sys.stdout

        outputResults( outfile, filtered_pairs, go2info, fdrs = fdrs, samples = samples )

        if options.output_filename_pattern:
            outfile.close()

        #############################################################
        ## output the full result
            
        if options.output_filename_pattern:
            filename = options.output_filename_pattern % { 'go': ontology, 'section': "overall" }
            E.info( "a list of all categories and pvalues goes to %s" % filename )
            outfile = IOTools.openFile(filename, "w", create_dir = True)
        else:
            outfile = sys.stdout
        
        outputResults( outfile, pairs, go2info, fdrs = fdrs, samples = samples )

        if options.output_filename_pattern:
            outfile.close()

        #############################################################
        ## output parameters
        ngenes, ncategories, nmaps = CountGO( gene2go )

        if options.output_filename_pattern:
            filename = options.output_filename_pattern % { 'go': ontology, 'section': "parameters" }
            if options.loglevel >= 1:
                options.stdlog.write( "# parameters go to %s\n" % filename )
            outfile = IOTools.openFile(filename, "w", create_dir = True)
        else:
            outfile = sys.stdout
            
        outfile.write( "# input go mappings for category '%s'\n" % ontology )
        outfile.write( "value\tparameter\n" )
        outfile.write( "%i\tmapped genes\n" % ngenes )
        outfile.write( "%i\tmapped categories\n" % ncategories )
        outfile.write( "%i\tmappings\n" % nmaps )

        nbackground = len(background)
        if nbackground == 0:
            nbackground = len(go_results.mBackgroundGenes)
            
        outfile.write( "%i\tgenes in sample\n" % len(genes) )
        outfile.write( "%i\tgenes in sample with GO assignments\n" % (len(go_results.mSampleGenes)) )
        outfile.write( "%i\tinput background\n" % nbackground )
        outfile.write( "%i\tgenes in background with GO assignments\n" % (len(go_results.mBackgroundGenes)) )
        outfile.write( "%i\tassociations in sample\n"     % go_results.mSampleCountsTotal )
        outfile.write( "%i\tassociations in background\n" % go_results.mBackgroundCountsTotal )
        outfile.write( "%s\tpercent genes in sample with GO assignments\n" % (IOTools.prettyPercent( len(go_results.mSampleGenes) , len(genes), "%5.2f" )))
        outfile.write( "%s\tpercent genes background with GO assignments\n" % (IOTools.prettyPercent( len(go_results.mBackgroundGenes), nbackground, "%5.2f" )))

        outfile.write( "%i\tsignificant results reported\n" % nselected )
        outfile.write( "%6.4f\tsignificance threshold\n" % options.threshold )        

        if options.output_filename_pattern:
            outfile.close()

        #############################################################
        ## output the fg patterns
            
        #############################################################
        ## Compute reverse map
        go2genes = {}

        for gene, gos in gene2go.items():
            if gene not in genes: continue
            for go in gos:
                if go.mGOId not in go2genes:
                    go2genes[go.mGOId] = []
                go2genes[go.mGOId].append( gene )
            
        if options.output_filename_pattern:
            filename = options.output_filename_pattern % { 'go': ontology, 'section': "fg" }
            if options.loglevel >= 1:
                options.stdlog.write( "# results go to %s\n" % filename )
            outfile = IOTools.openFile(filename, "w", create_dir = True)
        else:
            outfile = sys.stdout

        headers = ["code", "scount", "stotal", "spercent", "bcount", "btotal", "bpercent",
                   "ratio", 
                   "pvalue", "pover", "punder", "goid", "category", "description", "fg"]

        for k, v in pairs:

            code = GetCode( v )            

            if k in go2info:
                n = go2info[k]
            else:
                n = GOInfo()
                
            if k in go2genes:
                g = ";".join( go2genes[k] )
            else:
                g = ""

            outfile.write("%s\t%s\t%s\t%s\n" % (code, str(v), n, g ) )

        if outfile != sys.stdout:
            outfile.close()

    E.Stop()