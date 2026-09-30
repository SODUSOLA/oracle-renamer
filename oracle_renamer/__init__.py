"""
oracle_renamer
==============

Renames Oracle-numbered staff passport photos to LASSRA numbers using a
staff Excel/CSV sheet as the lookup source.

Pipeline (see docs for the full design):

    index images  ->  read rows  ->  build plan  ->  execute plan  ->  write report
    (BUILD phase)     (validate)     (PROBE phase)   (parallel I/O)    (audit trail)
"""

__version__ = "1.0.0"
