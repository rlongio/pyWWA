# Copyright (c) 2005-2008 Iowa State University
# http://mesonet.agron.iastate.edu/ -- mailto:akrherz@iastate.edu
#
# This program is free software; you can redistribute it and/or modify it under
# the terms of the GNU General Public License as published by the Free Software
# Foundation; either version 2 of the License, or (at your option) any later
# version.
#
# This program is distributed in the hope that it will be useful, but WITHOUT
# ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS
# FOR A PARTICULAR PURPOSE. See the GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License along with
# this program; if not, write to the Free Software Foundation, Inc.,
# 59 Temple Place - Suite 330, Boston, MA  02111-1307, USA.
""" SHEF product ingestor """

# Setup Standard Logging we use
from twisted.python import log, logfile
log.FileLogObserver.timeFormat = "%Y/%m/%d %H:%M:%S %Z"
log.startLogging(logfile.DailyLogFile('shef_parser.log', 'logs/'))

# System Imports
import os, smtplib
from email.mime.text import MIMEText

o = open("shef_parser.pid",'w')
o.write("%s" % ( os.getpid(),) )
o.close()

# Stuff I wrote
from pyIEM import mesonet
import access
from support import ldmbridge, TextProduct
import secret
import common

# Third Party Stuff
from twisted.internet import reactor, protocol
from twisted.enterprise import adbapi
import mx.DateTime


# Setup Database Links
ACCESSDB = adbapi.ConnectionPool("psycopg2", database='iem', host=secret.dbhost,
                                 password=secret.dbpass)
HADSDB = adbapi.ConnectionPool("psycopg2", database='hads', host=secret.dbhost,
                               password=secret.dbpass)
import pg
IEMACCESS = pg.connect('iem', secret.dbhost, user=secret.dbuser, passwd=secret.dbpass)
MESOSITE = pg.connect('mesosite', secret.dbhost, user=secret.dbuser, passwd=secret.dbpass)

BASE_TS = mx.DateTime.gmt() - mx.DateTime.RelativeDateTime(months=2)

# Necessary for the shefit program to run A-OK
os.chdir("/home/ldm/pyWWA/shef_workspace")

# Load up our lookup table of stations to networks
LOC2STATE = {}
LOC2NETWORK = {}
UNKNOWN = {}
rs = MESOSITE.query("""SELECT id, network, state from stations 
    WHERE network ~* 'COOP' or network ~* 'DCP' or network in ('KCCI','KIMT','KELO') 
    ORDER by network ASC""").dictresult()
for i in range(len(rs)):
    id = rs[i]['id']
    LOC2STATE[ id ] = rs[i]['state']
    if LOC2NETWORK.has_key(id):
        del LOC2NETWORK[id]
    else:
        LOC2NETWORK[id] = rs[i]['network']
MESOSITE.close()

MULTIPLIER = {
  "US" : 0.87,  # Convert MPH to KNT
  "UG": 0.87,
  "UP": 0.87,
  "UR": 10,
}

"""
Some notes on the SHEF codes translated to something IEM Access can handle, for now

First two chars are physical extent code

"""
DIRECTMAP = {'HGIZ': 'rstage',
             'HPIZ': 'rstage',
             'HTIZ': 'rstage',
             'PPHZ': 'phour',
             'TDIZ': 'dwpf',
             'TAIZ': 'tmpf',
             'TAIN': 'min_tmpf',
             'TAIX': 'max_tmpf',
             'PPDZ': 'pday',
             'PPPZ': 'pday',
             'PCIZ': 'pcounter',
             'RWIZ': 'srad',
             'SDIZ': 'snowd',
             'XRIZ': 'relh',
             'PAIZ': 'pres',
             
             'QRIZ': 'discharge',
             'QTIZ': 'discharge',
             
             'SWIZ': 'snoww',
             'USIZ': 'sknt',
             'SFIZ': 'snow',
             'SFDZ': 'snow',
             'UDIZ': 'drct',
             'UGIZ': 'gust',
             'UPIZ': 'gust',
             'UPHZ': 'gust',
             }
class mydict(dict):
    
    def __getitem__(self, key):
        """
        Over-ride getitem so that the sql generated is proper
        """
        val = self.get(key)
        if val is not None:
            return val
        # Logic to convert this key into something the iem can handle
        # Our key is always at least 6 chars!
        PEI = "%s%s" % (key[:3],key[5])
        if DIRECTMAP.has_key(PEI):
            self.__setitem__(key, DIRECTMAP[PEI])
            return DIRECTMAP[PEI]
        else:
            print 'Can not map var %s' % (key,)
            self.__setitem__(key, '')
            return ''
    
MAPPING = mydict()


EMAILS = 10

class MyIEMOB(access.Ob):
    """
    Override the iemAccessOb class with my own query engine, so that we 
    can capture errors for now!
    """

    def execQuery(self, sql, dbase, dbpool):
        """
        Execute queries with an error callback!
        """
        dbpool.runOperation( sql ).addErrback( common.email_error, sql)

class SHEFIT(protocol.ProcessProtocol):
    """
    My process protocol for dealing with the SHEFIT program from the NWS
    """

    def __init__(self, tp):
        """
        Constructor
        """
        self.tp = tp
        self.data = ""

    def connectionMade(self):
        """
        Fired when the program starts up and wants stdin
        """
        #print "sending %d bytes!" % len(self.shefdata)
        #print "SENDING", self.shefdata
        self.transport.write( self.tp.raw )
        self.transport.closeStdin()

    def outReceived(self, data):
        """
        Save the stdout we get from the program for later processing
        """
        #print "GOT", data
        self.data = self.data + data

    def errReceived(self, data):
        """
        In case something comes to stderr 
        """
        print "errReceived! with %d bytes!" % len(data)
        print data

#    def processEnded(self, status):
#        print "debug: type(status): %s" % type(status.value)
#        print "error: exitCode: %s" % status.value.exitCode


    def outConnectionLost(self):
        """
        Once the program is done, we need to do something with the data
        """
        if self.data == "":
            rejects = open("empty.shef", 'a')
            rejects.write( self.tp.raw +"\003")
            rejects.close()
            return
        try:
            really_process(self.tp, self.data)
        except Exception,myexp:
            common.email_error(myexp, self.tp.raw)

def clnstr(buf):
    buf = buf.replace("\015\015\012", "\n")
    buf = buf.replace("\003", "")
    return buf.replace("\001", "")

class MyProductIngestor(ldmbridge.LDMProductReceiver):

    def connectionLost(self, reason):
        print 'connectionLost', reason
        reactor.callLater(5, self.shutdown)

    def shutdown(self):
        reactor.callWhenRunning(reactor.stop)

    def process_data(self, buf):
        """
        I am called from the ldmbridge when data is ahoy
        """
        tp = TextProduct.TextProduct( clnstr(buf) )
        shef = SHEFIT( tp )
        reactor.spawnProcess(shef, "shefit", ["shefit"], {})



def really_process(tp, data):
    """
    This processes the output we get from the SHEFIT program
    """
    # Now we loop over the data we got :)
    mydata = {}
    for line in data.split("\n"):
        # Skip blank output lines
        if line.strip() == "":
            continue
        tokens = line.split()
        if len(tokens) < 7:
            print "NO ENOUGH TOKENS", line
            continue
        sid = tokens[0]
        if len(sid) > 8:
            print "SiteID Len Error: [%s] %s" % (sid, tp.get_product_id())
            continue
        if not mydata.has_key(sid):
            mydata[sid] = {}
        dstr = "%s %s" % (tokens[1], tokens[2])
        tstamp = mx.DateTime.strptime(dstr, "%Y-%m-%d %H:%M:%S")
        # We don't care about data in the future!
        if tstamp > (mx.DateTime.gmt() + mx.DateTime.RelativeDateTime(hours=1)):
            continue
        if tstamp < BASE_TS:
            log.msg("Rejecting old data %s %s" % (sid, tstamp))
            continue
        if not mydata[sid].has_key(tstamp):
            mydata[sid][tstamp] = {}

        varname = tokens[5]
        value = float(tokens[6])
        mydata[sid][tstamp][varname] = value

    # Now we process each station we found in the report! :)
    for sid in mydata.keys():
        times = mydata[sid].keys()
        times.sort()
        for tstamp in times:
            process_site(tp, sid, tstamp, mydata[sid][tstamp])

def enter_unknown(sid, tp, network):
    """
    Enter some info about a site ID we know nothing of...
    """
    HADSDB.runOperation("""
            INSERT into unknown(nwsli, product, network) 
            values ('%s', '%s', '%s')
        """ % (sid, tp.get_product_id() , network))

def checkvars( vars ):
    """
    Check variables to see if we have a COOP or DCP site
    """
    for v in vars:
        # Definitely DCP
        if v[:2] in ['HG',]:
            return False
        if v[:2] in ['SF','SD']:
            return True
        if v[:3] in ['PPH',]:
            return False
    return False

def process_site(tp, sid, ts, data):
    """ 
    I process a dictionary of data for a particular site
    """

    # Our simple determination if the site is a COOP site
    isCOOP = False
    if tp.afos[:3] == 'RR3':
        isCOOP = True
    elif tp.afos[:3] in ['RR1', 'RR2'] and checkvars( data.keys() ):
        print "Guessing COOP? %s %s %s" %  (sid, tp.get_product_id(), data.keys())
        isCOOP = True

    for var in data.keys():
        HADSDB.runOperation("""INSERT into raw%s 
            (station, valid, key, value) 
            VALUES('%s','%s+00', '%s', '%s')""" % (ts.strftime("%Y_%m"), sid, 
            ts.strftime("%Y-%m-%d %H:%M"), var, 
            data[var])).addErrback(common.email_error, tp)

    state = LOC2STATE.get( sid )
    # TODO, someday support processing these stranger locations
    if state is None and len(sid) == 8 and sid[0] == 'X':
        return
    if state is None and len(sid) == 5 and mesonet.nwsli2state.has_key(sid[3:]):
        state = mesonet.nwsli2state.get(sid[3:])
        LOC2STATE[sid] = state
    if state is None:
        if UNKNOWN.get(sid) is None:
            enter_unknown(sid, tp, "")
            UNKNOWN[sid] = 1       
        return 
    
    #print sid, state, isCOOP
    # Deterime if we want to waste the DB's time
    network = LOC2NETWORK.get(sid)
    if network in ['KCCI','KIMT','KELO']:
        return
    if network is None:
        if isCOOP:
            network = "%s_COOP" % (state,)
        # We are left with DCP
        else:
            network = "%s_DCP" % (state,)

    
    # Lets do this, finally.
    #print "ACTUALLY PROCESSED", sid, network
    iemob = MyIEMOB(sid, network)
    iemob.setObTimeGMT(ts)
    iemob.data['year'] = ts.year
    if not iemob.load_and_compare(IEMACCESS) and ts.strftime("%Y%m%d") == mx.DateTime.now().strftime("%Y%m%d"):
        #print 'Unknown StationID %s %s' %  (sid,  tp.get_product_id() )
        enter_unknown(sid, tp, network)
        
    for var in data.keys():
        myval = data[var] * MULTIPLIER.get(var[:2],1.0)
        iemob.data[ MAPPING[var] ] = myval
    iemob.data['raw'] = tp.get_product_id()
    iemob.update_summary(None, ACCESSDB)
    # Don't go through this pain, unless we need to!
    if 'max_tmpf' in iemob.data.keys():
        iemob.updateDatabaseSummaryTemps(None, ACCESSDB)
    iemob.updateDatabase(None, ACCESSDB)

    del iemob

#
fact = ldmbridge.LDMProductFactory( MyProductIngestor() )
reactor.run()
