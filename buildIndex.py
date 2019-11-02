#!/usr/bin/python
"""send diarybot entries to search."""
import requests
import json
from pymongo import MongoClient as Connection
from chatinterface import ChatInterface
from rdflib import Graph
from bot import makeBots

configGraph = Graph()
configGraph.parse('bots-secret.n3', format='n3')
chat = ChatInterface(lambda *a: None)
bots = makeBots(chat, configGraph)


for botName in ['aribot', 'asherbot']:
    coll = Connection('bang', 27017)['diarybot'][botName]
    for row in coll.find():
        txt = row['sioc:content']
        uri = bots[botName].uriForDoc(row)

        label = {
            'http://bigasterisk.com/kelsi/foaf.rdf#kelsi': 'Kelsi',
            'http://bigasterisk.com/foaf.rdf#drewp': 'Drew',
        }.get(row['dc:creator'], row['dc:creator'])

        doc = dict(uri=uri,
                   title='%s entry by %s at %s' % (
                       botName, label, row['dc:created']),
                   text=txt)
        requests.post(
            'http://bang:9096/index',
            params={
                'source': botName},
            data=json.dumps(doc)).raise_for_status()
