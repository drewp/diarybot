#!/usr/bin/python
"""
send diarybot entries to search
"""
from __future__ import division
import restkit, json, os, re
from pymongo import MongoClient as Connection

search = restkit.Resource("http://bang:9096/")

coll = Connection('bang', 27017)['diarybot']['aribot']
for row in coll.find():
    txt = row['sioc:content']
    # todo
    uri = "http://bigasterisk.com/diarybot/%s/%s" % ('aribot', row['dc:created'])

    label = {
        'http://bigasterisk.com/kelsi/foaf.rdf#kelsi' : 'Kelsi',
        'http://bigasterisk.com/foaf.rdf#drewp' : 'Drew',
        }.get(row['dc:creator'], row['dc:creator'])
    
    doc = dict(uri=uri,
               title="Entry by %s at %s" % (label,
                                            row['dc:created']),
               text=txt)
    search.post("index", source="aribot", payload=json.dumps(doc))
