""" SPENES product ingestor """

from twisted.python import log, logfile
import os
log.FileLogObserver.timeFormat = "%Y/%m/%d %H:%M:%S %Z"
log.startLogging( logfile.DailyLogFile('spe_parser.log', 'logs') )


import sys
import re
import common

from twisted.internet import reactor

import ConfigParser
config = ConfigParser.ConfigParser()
config.read(os.path.join(os.path.dirname(__file__), 'cfg.ini'))

from pyiem.nws import product
POSTGIS = common.get_database('postgis')
raw = sys.stdin.read()

def process(raw):
    try:
        real_process(raw)
    except Exception, exp:
        common.email_error(exp, raw)


def real_process(raw):
    sqlraw = raw.replace("\015\015\012", "\n")
    prod = product.TextProduct(raw)

    product_id = prod.get_product_id()
    xtra ={
           'product_id': product_id,
           'channels': []
           }
    sql = """INSERT into text_products(product, product_id) values (%s,%s)"""
    myargs = (sqlraw, product_id)
    POSTGIS.runOperation(sql, myargs)
    
    tokens = re.findall("ATTN (WFOS|RFCS)(.*)", raw)
    for tpair in tokens:
        wfos = re.findall("([A-Z]+)\.\.\.", tpair[1])
        xtra['channels'] = []
        for wfo in wfos:
            xtra['channels'].append( wfo )
            twt = "#%s NESDIS issues Satellite Precipitation Estimates" % (wfo,)
            url = "%s?pid=%s" % (config.get('urls', 'product'), product_id)
            common.tweet(wfo, twt, url)

        body = "NESDIS issues Satellite Precipitation Estimates %s?pid=%s" % (
                config.get('urls', 'product'), product_id)
        htmlbody = "NESDIS issues <a href='%s?pid=%s'>Satellite Precipitation Estimates</a>" %(
                config.get('urls', 'product'), product_id)
        jabber.sendMessage(body, htmlbody, xtra)




def killer():
    reactor.stop()

jabber = common.make_jabber_client("spe_parser")
reactor.callLater(0, process, raw)
reactor.callLater(30, killer)
reactor.run()



