#!/usr/bin/python
"""
send diarybot entries to search
"""
from __future__ import division
import requests, json, os, re
from pymongo import MongoClient as Connection

for bot in ['aribot', 'asherbot']:
    coll = Connection('bang', 27017)['diarybot'][bot]
    for row in coll.find():
        txt = row['sioc:content']
        # todo
        uri = "http://bigasterisk.com/diarybot/%s/%s" % (bot, row['dc:created'])

        label = {
            'http://bigasterisk.com/kelsi/foaf.rdf#kelsi' : 'Kelsi',
            'http://bigasterisk.com/foaf.rdf#drewp' : 'Drew',
            }.get(row['dc:creator'], row['dc:creator'])

        doc = dict(uri=uri,
                   title="Entry by %s at %s" % (label,
                                                row['dc:created']),
                   text=txt)
        requests.post("http://bang:9096/index", params={'source': 'bot'}, data=json.dumps(doc))
