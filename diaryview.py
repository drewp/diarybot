#!/usr/bin/python
"""
display diary entries
"""
import sys, web, restkit
from web.contrib.template import render_genshi
from rdflib import URIRef
sys.path.append(".")
from diarybot import RdfStore
import xml.utils.iso8601
import datetime
render = render_genshi('.', auto_reload=True)

def getLoginBar():
    openidProxy = restkit.Resource("http://bang:9023/")
    return openidProxy.get("_loginBar",
                 headers={"Cookie" : web.ctx.environ.get('HTTP_COOKIE', '')})

urls = (r'/', "index",
        )

app = web.application(urls, globals(), autoreload=True)
application = app.wsgifunc()

class index(object):
    def GET(self):
        web.header('Content-type', 'application/xhtml+xml')

        agent = URIRef(web.ctx.environ['HTTP_X_FOAF_AGENT'])
        # todo- look up available bot uris for this agent
        store = RdfStore(bot)

        entries = []
        for created, creator, content in store.queryData("""
          SELECT ?created ?creator ?content WHERE {
            [ a sioc:Post;
              dc:creator ?creator;
              dc:created ?created;
              sioc:content ?content 
              ]
          } ORDER BY desc(?created)"""):
            entries.append((created, creator, content))

        def prettyDate(iso):
            t = xml.utils.iso8601.parse(str(iso))
            d = datetime.date.fromtimestamp(t)
            return d.strftime("%Y-%m-%d %a")

        
        return render.diaryview(
            bot=bot,
            agent=agent,
            entries=entries,
            prettyDate=prettyDate,
            loginBar=getLoginBar())
    
if __name__ == '__main__':
    sys.argv.append("9048") 
    app.run()

