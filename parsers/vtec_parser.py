"""
VTEC product ingestor

The warnings table has the following timestamp based columns, this gets ugly
with watches.  Lets try to explain

    issue   <- VTEC timestamp of when this event was valid for
    expire  <- When does this VTEC product expire
    updated <- Product Timestamp of when a product gets updated
    init_expire <- When did this product initially expire
    product_issue <- When was this product issued by the NWS
"""

# Twisted Python imports
from syslog import LOG_LOCAL2
from twisted.python import syslog
syslog.startLogging(prefix='pyWWA/vtec_parser', facility=LOG_LOCAL2)
from twisted.python import log
from twisted.internet import reactor

# http://stackoverflow.com/questions/7016602
from twisted.web.client import HTTPClientFactory
HTTPClientFactory.noisy = False
from twisted.mail.smtp import SMTPSenderFactory
SMTPSenderFactory.noisy = False

# Standard Python modules
import re
import datetime
import sys

# third party
import pytz

# pyLDM https://github.com/akrherz/pyLDM
from pyldm import ldmbridge
# pyIEM https://github.com/akrherz/pyIEM
from pyiem.nws.products.vtec import parser as vtecparser
from pyiem.nws.product import TextProductException
from pyiem.nws import ugc
from pyiem.nws import nwsli

import common


ugc_dict = {}
nwsli_dict = {}


def shutdown():
    ''' Stop this app '''
    log.msg("Shutting down...")
    reactor.callWhenRunning(reactor.stop)


# LDM Ingestor
class MyProductIngestor(ldmbridge.LDMProductReceiver):
    """ I receive products from ldmbridge and process them 1 by 1 :) """

    def connectionLost(self, reason):
        ''' callback when the stdin reader connection is closed '''
        log.msg('connectionLost() called...')
        log.err(reason)
        reactor.callLater(7, shutdown)

    def process_data(self, buf):
        """ Process the product """
        try:
            really_process_data(buf)
        except TextProductException, (channel, mess):
            if not MANUAL:
                jabber.sendMessage(mess, mess, {'channels': '%s,%s' % (channel,
                                                                       "ERROR")
                                                }
                                   )
        except Exception, myexp:  # pylint: disable=W0703
            common.email_error(myexp, buf)


def really_process_data(buf):
    ''' Actually do some processing '''
    gmtnow = datetime.datetime.utcnow()
    gmtnow = gmtnow.replace(tzinfo=pytz.timezone("UTC"))

    # Make sure we have a trailing $$, if not report error and slap one on
    if buf.find("$$") == -1:
        common.email_error("No $$ Found!", buf)
        buf += "\n\n$$\n\n"

    # Create our TextProduct instance
    text_product = vtecparser(buf, utcnow=gmtnow, ugc_provider=ugc_dict,
                              nwsli_provider=nwsli_dict)
    # Skip spanish products
    if text_product.source == 'TJSJ' and text_product.afos[3:] == 'SPN':
        return

    df = PGCONN.runInteraction(text_product.sql)
    df.addCallback(step2, text_product)
    df.addErrback(common.email_error, text_product.unixtext)
    df.addErrback(log.err)


def step2(dummy, text_product):
    ''' After the SQL is done, lets do other things '''
    if len(text_product.warnings) > 0:
        common.email_error("\n\n".join(text_product.warnings),
                           text_product.text)

    # Do the Jabber work necessary after the database stuff has completed
    for (plain, html, xtra) in text_product.get_jabbers(
            common.settings.get('pywwa_vtec_url', 'pywwa_vtec_url'),
            common.settings.get('pywwa_river_url', 'pywwa_river_url')):
        if xtra.get('channels', '') == '':
            common.email_error("xtra[channels] is empty!", text_product.text)
        if not MANUAL:
            jabber.sendMessage(plain, html, xtra)


def load_ugc(txn):
    """ load ugc"""
    sql = """SELECT name, ugc, wfo from ugcs WHERE
        name IS NOT Null and end_ts is null"""
    txn.execute(sql)
    for row in txn:
        nm = (row["name"]).replace("\x92", " ").replace("\xc2", " ")
        wfos = re.findall(r'([A-Z][A-Z][A-Z])', row['wfo'])
        ugc_dict[row['ugc']] = ugc.UGC(row['ugc'][:2], row['ugc'][2],
                                       row['ugc'][3:],
                                       name=nm,
                                       wfos=wfos)

    log.msg("ugc_dict loaded %s entries" % (len(ugc_dict),))

    sql = """SELECT nwsli,
     river_name || ' ' || proximity || ' ' || name || ' ['||state||']' as rname
     from hvtec_nwsli"""
    txn.execute(sql)
    for row in txn:
        nm = row['rname'].replace("&", " and ")
        nwsli_dict[row['nwsli']] = nwsli.NWSLI(row['nwsli'],
                                               name=nm)

    log.msg("nwsli_dict loaded %s entries" % (len(nwsli_dict),))

    return None


def ready(dummy):
    ''' cb when our database work is done '''
    ldmbridge.LDMProductFactory(MyProductIngestor(dedup=True))

if __name__ == '__main__':

    MANUAL = False
    if len(sys.argv) == 2 and sys.argv[1] == 'manual':
        log.msg("Manual runtime (no jabber, 1 database connection) requested")
        MANUAL = True

    # Fire up!
    PGCONN = common.get_database(common.config['databaserw']['postgis'],
                                 cp_max=(5 if not MANUAL else 1))
    df = PGCONN.runInteraction(load_ugc)
    df.addCallback(ready)
    df.addErrback(common.email_error, "load_ugc failure!")
    jabber = common.make_jabber_client('vtec_parser')

    reactor.run()
