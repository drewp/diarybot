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
    search.post("index", source="aribot", title="Entry by %s at %s" % (row['dc:creator'], row['dc:created']), payload=json.dumps(dict(uri=uri, text=txt)))
