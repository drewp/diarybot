#!/usr/bin/python
"""
send diarybot entries to search
"""
from __future__ import division
import restkit, json, os, re
from pymongo import Connection

search = restkit.Resource("http://bang:8080/search_2.8.1-1.0.3/")

coll = Connection('bang', 27017)['diarybot']['aribot']
for row in coll.find():
    txt = row['sioc:content']
    # todo
    uri = "http://bigasterisk.com/diarybot/%s/%s" % ('aribot', row['dc:created'])
    doc = dict(uri=uri,
               title="Entry by %s at %s" % (row['dc:creator'],
                                            row['dc:created']),
               text=txt)
    search.post("index", source="aribot", payload=json.dumps(doc))
