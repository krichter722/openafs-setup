# -*- coding: utf-8 -*-

#    This program is free software: you can redistribute it and/or modify
#    it under the terms of the GNU General Public License as published by
#    the Free Software Foundation, either version 3 of the License, or
#    (at your option) any later version.
#
#    This program is distributed in the hope that it will be useful,
#    but WITHOUT ANY WARRANTY; without even the implied warranty of
#    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#    GNU General Public License for more details.
#
#    You should have received a copy of the GNU General Public License
#    along with this program.  If not, see <http://www.gnu.org/licenses/>.
#
#    Dieses Programm ist Freie Software: Sie können es unter den Bedingungen
#    der GNU General Public License, wie von der Free Software Foundation,
#    Version 3 der Lizenz oder (nach Ihrer Wahl) jeder neueren
#    veröffentlichten Version, weiterverbreiten und/oder modifizieren.
#
#    Dieses Programm wird in der Hoffnung, dass es nützlich sein wird, aber
#    OHNE JEDE GEWÄHRLEISTUNG, bereitgestellt; sogar ohne die implizite
#    Gewährleistung der MARKTFÄHIGKEIT oder EIGNUNG FÜR EINEN BESTIMMTEN ZWECK.
#    Siehe die GNU General Public License für weitere Details.
#
#    Sie sollten eine Kopie der GNU General Public License zusammen mit diesem
#    Programm erhalten haben. Wenn nicht, siehe <http://www.gnu.org/licenses/>.

# Reference implementation of the OpenAFS Quick Start Guide at
# http://docs.openafs.org/QuickStartUnix.pdf.
#
# Limitations:
# - currently ignores value of `prefix` variable and uses `/usr/local`

from __future__ import absolute_import
import subprocess as sp
import signal
import os
import template_helper
import sys
import logging
import pexpect
import shutil
import plac
import ast
import re
import getpass
import threading

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
logger_stdout_handler = logging.StreamHandler()
logger_stdout_handler.setLevel(logging.INFO)
logger_formatter = logging.Formatter('%(asctime)s:%(message)s')
logger_stdout_handler.setFormatter(logger_formatter)
logger.addHandler(logger_stdout_handler)

# installation/`configure` prefix (ignored for configuration files in this script if `transarc` is `True`)
prefix_default="/usr/local"

# kerberos pathes should be adjusted by PATH automatically
krb_version = (1,14)
if krb_version < (1,14):
    newrealm_cmds = ["krb5_newrealm"] # a list to support command and subcommand in 1.14
else:
    newrealm_cmds = ["kdb5_util", "create"]
kadmin_local = "kadmin.local"
krb5kdc = "krb5kdc"
kadmind = "kadmind"
kinit = "kinit"
kvno = "kvno"
kdb5_util = "kdb5_util"
klist = "klist"
service = "service"

upgrade = False # True when running after upgrading AFS
cache_dir_path = "/var/cache/openafs"
PATH_MODE_UBUNTU = "ubuntu"
PATH_MODE_SOURCE = "source"
PATH_MODE_TRANSARC = "transarc"
PATH_MODES = set([PATH_MODE_UBUNTU, PATH_MODE_SOURCE, PATH_MODE_TRANSARC])
KRB_PATH_MODE_UBUNTU = "ubuntu"
KRB_PATH_MODE_SOURCE = "source"
KRB_PATH_MODES = set([KRB_PATH_MODE_UBUNTU, KRB_PATH_MODE_SOURCE])

bosserver_proc = None

def __pexpect_spawn__(cmds):
    if type(cmds) != type([]):
        raise ValueError("cmds has to be a list, but is a %s" % (str(type(cmds),)))
    logger.info("executing '%s' as pexpect process" % (str(cmds),))
    ret_value = pexpect.spawn(str.join(" ", cmds))
    ret_value.logfile_read = sys.stdout
    ret_value.timeout = 100000
    return ret_value

def __sp_check_call__(cmds, no_fail):
    if type(cmds) != type([]):
        raise ValueError("cmds has to be a list, but is a %s" % (str(type(cmds),)))
    logger.info("executing '%s' as subprocess" % (str(cmds),))
    try:
        sp.check_call(cmds)
    except sp.CalledProcessError as ex:
        if no_fail:
            logger.warn(str(ex))
        else:
            raise ex

def __sp_popen__(cmds):
    if type(cmds) != type([]):
        raise ValueError("cmds has to be a list, but is a %s" % (str(type(cmds),)))
    logger.info("executing '%s' as background subprocess" % (str(cmds),))
    return sp.Popen(cmds)

@plac.annotations(path_mode=plac.Annotation("System packages and source installations provide different static pathes (in Ubuntu used `/etc/openafs`, source installation can use `[prefix]/etc/openafs` or `/usr/vice` if OpenAFS has been built with `--enable-transarc-paths` (recommended in order to follow the QuickStart guide and avoid failure of kernel module loading))", "positional", type=str, choices=PATH_MODES), # needs to be positional in order to enforce specification
    krb_path_mode=plac.Annotation("The pathes to use for kerberos", "positional", type=str, choices=KRB_PATH_MODES),
    machine_name=plac.Annotation("The machine name to use", "positional"),
    cell_name=plac.Annotation("The cell name to use", "positional"),
    cell_ip=plac.Annotation("The IPv4 address of the cell referred to by the cell name", "positional"), #@TODO: this should be determined automatically and be an option only
    krb_realm=plac.Annotation("The kerberos realm (hostname) to configure and use for OpenAFS", "positional"),
    krb_pw=plac.Annotation("The kerberos master password to use (you'll be prompted for input if omitted)", "option"),
    admin_pw=plac.Annotation("The AFS admin password to use (you'll be prompted for input if omitted)", "option"),
    skip_check_output=plac.Annotation("A flag indicating that the output of configuration files ought not to be checked with a difftool (useful for integration tests)", "flag"),
    no_fail=plac.Annotation("A flag indicating that failing command ought to not cause a failure of the script (useful to figure out whether a CI service supports all commands)", "flag"),
)
def openafs_setup(path_mode, krb_path_mode, machine_name, cell_name, cell_ip, krb_realm, krb_pw=None, admin_pw=None, skip_check_output=False, no_fail=False):
    if not path_mode in PATH_MODES:
        raise ValueError("path_mode has to be one of %s" % (str(PATH_MODES),))
    logger.info("using path mode %s" % (path_mode,))
    # binaries
    if path_mode == PATH_MODE_TRANSARC:
        bosserver = "/usr/afs/bin/bosserver"
        bos = "/usr/afs/bin/bos"
        asetkey = "/usr/afs/bin/asetkey"
        pts = "/usr/afs/bin/pts"
        vos = "/usr/afs/bin/vos"
        buserver = "/usr/afs/bin/buserver"
        ptserver = "/usr/afs/bin/ptserver"
        vlserver = "/usr/afs/bin/vlserver"
        fileserver = "/usr/afs/bin/fileserver"
        volserver = "/usr/afs/bin/volserver"
        salvager = "/usr/afs/bin/salvager"
        dafileserver = "/usr/afs/bin/fileserver"
        davolserver = "/usr/afs/bin/volserver"
        salvageserver = "/usr/afs/bin/salvageserver"
        dasalvager = "/usr/afs/bin/salvager"
        upserver = "/usr/afs/bin/upserver"
        keytab_file_path = "/usr/vice/etc/afs.keytab"
        cellservdb_server_file_path = "/usr/vice/etc/server/CellServDB"
        thiscell_server_file_path = "/usr/vice/etc/server/ThisCell"
        cellservdb_client_file_path = "/usr/vice/etc/CellServDB"
        thiscell_client_file_path = "/usr/vice/etc/ThisCell"
        cacheinfo_file_path = "/usr/vice/etc/cacheinfo"
        keytab_file_encryption = "aes256-cts-hmac-sha1-96:normal,aes128-cts-hmac-sha1-96:normal"
    elif path_mode == PATH_MODE_SOURCE:
        bosserver = "bosserver"
        bos = "bos"
        asetkey = "asetkey"
        pts = "pts"
        vos = "vos"
        buserver = "/usr/local/libexec/openafs/buserver"
        ptserver = "/usr/local/libexec/openafs/ptserver"
        vlserver = "/usr/local/libexec/openafs/vlserver"
        fileserver = "/usr/local/libexec/openafs/fileserver"
        volserver = "/usr/local/libexec/openafs/volserver"
        salvager = "/usr/local/libexec/openafs/salvager"
        dafileserver = "/usr/local/libexec/openafs/fileserver"
        davolserver = "/usr/local/libexec/openafs/volserver"
        salvageserver = "/usr/local/libexec/openafs/salvageserver"
        dasalvager = "/usr/local/libexec/openafs/salvager"
        upserver = "/usr/local/libexec/openafs/upserver"
        keytab_file_path = "/usr/local/etc/openafs/afs.keytab"
        cellservdb_server_file_path = "/usr/local/etc/openafs/server/CellServDB"
        thiscell_server_file_path = "/usr/local/etc/openafs/server/ThisCell"
        cellservdb_client_file_path = "/usr/local/etc/openafs/CellServDB"
        thiscell_client_file_path = "/usr/local/etc/openafs/ThisCell"
        cacheinfo_file_path = "/usr/local/etc/openafs/cacheinfo"
        keytab_file_encryption = "aes256-cts-hmac-sha1-96:normal,aes128-cts-hmac-sha1-96:normal"
    elif path_mode == PATH_MODE_UBUNTU:
        bosserver = "/usr/sbin/bosserver"
        bos = "/usr/bin/bos"
        asetkey = "/usr/sbin/asetkey"
        pts = "/usr/bin/pts"
        vos = "/usr/bin/vos"
        buserver = "/usr/lib/openafs/buserver"
        ptserver = "/usr/lib/openafs/ptserver"
        vlserver = "/usr/lib/openafs/vlserver"
        fileserver = "/usr/lib/openafs/fileserver"
        volserver = "/usr/lib/openafs/volserver"
        salvager = "/usr/lib/openafs/salvager"
        dafileserver = "/usr/lib/openafs/fileserver"
        davolserver = "/usr/lib/openafs/volserver"
        salvageserver = "/usr/lib/openafs/salvageserver"
        dasalvager = "/usr/lib/openafs/salvager"
        upserver = "/usr/lib/openafs/upserver"
        keytab_file_path = "/etc/openafs/afs.keytab"
        cellservdb_server_file_path = "/etc/openafs/server/CellServDB"
        thiscell_server_file_path = "/etc/openafs/server/ThisCell"
        cellservdb_client_file_path = "/etc/openafs/CellServDB"
        thiscell_client_file_path = "/etc/openafs/ThisCell"
        cacheinfo_file_path = "/etc/openafs/cacheinfo"
        keytab_file_encryption = "des-cbc-crc:v4" # even `openafs-krb5` 1.6.15-1ubuntu1 on Ubuntu 16.04 only supports `des-cbc-crc:v4` according to `man asetkey` (reported enhancement at https://bugs.launchpad.net/ubuntu/+source/openafs/+bug/1581880)
            # "des-cbc-crc:afs3" suggested by older version of quick start guide, seems to cause `/usr/sbin/asetkey: unknown RPC error (-1765328203) for keytab entry with Principal afs@test, kvno 2, DES-CBC-CRC/MD5/MD4`
        logger.info("using keytab file encryption %s which is the only encryption supported by Ubuntu according to `man asetkey`" % (keytab_file_encryption,))
    else:
        raise ValueError("path_mode '%s' isn't supported" % (path_mode,))
    if krb_path_mode == KRB_PATH_MODE_SOURCE:
        krb_acl_file_path = "/usr/local/var/krb5kdc/kadm5.acl"
        krb5_conf_file_path = "/usr/local/etc/krb5/krb5.conf"
    elif krb_path_mode == KRB_PATH_MODE_UBUNTU:
        krb_acl_file_path = "/etc/kadm5.acl"
        krb5_conf_file_path = "/etc/krb5.conf"
    else:
        raise ValueError("krb_path_mode '%s' isn't supported" % (krb_path_mode,))

    # validate parameters
    if buserver == None:
        raise ValueError("buserver mustn't be None")
    if not os.path.exists(buserver):
        raise ValueError("buserver '%s' doesn't exist" % (buserver,))
    if ptserver == None:
        raise ValueError("ptserver mustn't be None")
    if not os.path.exists(ptserver):
        raise ValueError("ptserver '%s' doesn't exist" % (ptserver,))
    if vlserver == None:
        raise ValueError("vlserver mustn't be None")
    if not os.path.exists(vlserver):
        raise ValueError("vlserver '%s' doesn't exist" % (vlserver,))
    if machine_name is None:
        raise ValueError("machine_name mustn't be None")
    if cell_name is None:
        raise ValueError("cell_name musn't be None")
    if krb_pw is None:
        krb_pw = getpass.getpass("Kerberos password:")
    else:
        logger.warn("specifying -krb-pw on the command line is a security risk")
    if admin_pw is None:
        admin_pw = getpass.getpass("AFS admin password:")
    else:
        logger.warn("specifying -admin-pw on the command line is a security risk")
    # krb5 setup (needs `allow_weak_crypto = true`<ref>http://docs.openafs.org/ReleaseNotesWindows/Kerberos_v5_Requirements.html</ref>)
    template_helper.write_template_file("""[libdefaults]
	default_realm = %(krb_realm)s
	allow_weak_crypto = true
	dns_lookup_realm = true
	dns_lookup_kdc = true

[realms]
    # use "kdc = ..." if realm admins haven't put SRV records into DNS
	%(krb_realm)s = {
		kdc = %(krb_realm)s
		admin_server = %(krb_realm)s
		default_domain = %(krb_realm)s
	}

[logging]
	kdc = CONSOLE
""" % {"krb_realm": krb_realm}, krb5_conf_file_path, check_output=not skip_check_output)
    # CellServDB setup
    cellservdb_content = """>%s        #%s
%s                   #%s
>grand.central.org      #GCO Public CellServDB 01 Jan 2016
18.9.48.14                      #grand.mit.edu
128.2.13.219                    #grand-old-opry.central.org
>wu-wien.ac.at          #University of Economics, Vienna, Austria
137.208.3.33                    #goya.wu-wien.ac.at
137.208.7.57                    #caravaggio.wu-wien.ac.at
137.208.8.14                    #vermeer.wu-wien.ac.at
>hephy.at               #hephy-vienna
193.170.243.10                  #afs01.hephy.oeaw.ac.at
193.170.243.12                  #afs02.hephy.oeaw.ac.at
193.170.243.14                  #afs03.hephy.oeaw.ac.at
>cgv.tugraz.at          #CGV cell
129.27.218.30                   #phobos.cgv.tugraz.at
129.27.218.31                   #deimos.cgv.tugraz.at
129.27.218.32                   #trinculo.cgv.tugraz.at
>itp.tugraz.at          #Institute of Theoretical and Computational Physics, TU Graz, Aus
129.27.161.7                    #faepafs1.tu-graz.ac.at
129.27.161.15                   #faepafs2.tu-graz.ac.at
129.27.161.114                  #faepafs3.tu-graz.ac.at
>sums.math.mcgill.ca    #Society of Undergraduate Mathematics Students of McGill Universi
132.216.24.122                  #germain.sums.math.mcgill.ca
132.216.24.125                  #turing.sums.math.mcgill.ca
>ualberta.ca            #University of Alberta
129.128.1.131                   #file13.ucs.ualberta.ca
129.128.98.17                   #mystery.ucs.ualberta.ca
129.128.125.40                  #drake.ucs.ualberta.ca
>cern.ch                #European Laboratory for Particle Physics, Geneva
137.138.128.148                 #afsdb1.cern.ch
137.138.246.50                  #afsdb3.cern.ch
137.138.246.51                  #afsdb2.cern.ch
>ams.cern.ch            #AMS Experiment
137.138.188.185                 #ams.cern.ch
137.138.199.58                  #pcamsf4.cern.ch
>epfl.ch                #Swiss Federal Institute of Technology at Lausanne
128.178.109.111                 #kd1.epfl.ch
128.178.109.112                 #kd2.epfl.ch
128.178.109.113                 #kd3.epfl.ch
>ethz.ch                #Swiss Federal Inst. of Tech. - Zurich, Switzerland
82.130.118.32                   #afs-db-1.ethz.ch
>psi.ch                 #Paul Scherrer Institut - Villigen, Switzerland
129.129.190.140                 #afs00.psi.ch
129.129.190.141                 #afs01.psi.ch
129.129.190.142                 #afs02.psi.ch
>extundo.com            #Simon Josefsson's cell
195.42.214.241                  #slipsten.extundo.com
>freedaemon.com         #Free Daemon Consulting, Oklahoma City, OK, USA
66.210.104.254                  #afs0.freedaemon.com
>membrain.com           #membrain.com
66.93.118.125                   #stormy
130.85.24.11                    #weasel
130.85.24.13                    #straykitten
>nilcons.com            #nilcons.com
5.9.14.177                      #files.nilcons.com
>sodre.cx               #Sodre.cx
128.8.140.165                   #greed.sodre.cx
>ruk.cuni.cz            #Charles University Computer Centre, Prague, CR
195.113.0.36                    #asterix.ruk.cuni.cz
195.113.0.37                    #obelix.ruk.cuni.cz
195.113.0.40                    #sal.ruk.cuni.cz
>ics.muni.cz            #Masaryk university, Czech Republic
147.251.3.11                    #grond.ics.muni.cz
147.251.9.9                     #smaug2.ics.muni.cz
195.113.214.4                   #tarkil-xen.cesnet.cz
>zcu.cz                 #University of West Bohemia, Czech Republic
147.228.10.18                   #sauron.zcu.cz
147.228.52.10                   #oknos.zcu.cz
147.228.52.17                   #nic.zcu.cz
>desy.de                #Deutsches Elektronen-Synchrotron
131.169.2.19                    #afsdb2.desy.de
131.169.2.20                    #afsdb3.desy.de
131.169.244.60                  #solar00.desy.de
>naf.desy.de            #National Analysis Facility at DESY
141.34.220.32                   #tcsh1-vm1.naf.desy.de
141.34.230.33                   #tcsh2-vm1.naf.desy.de
141.34.230.34                   #tcsh3-vm1.naf.desy.de
>gppc.de                #GPP Chemnitz mbH
213.187.92.33                   #gpp1.gppc.de
213.187.92.34                   #paulchen.gppc.de
213.187.92.35                   #lotus.gppc.de
>cms.hu-berlin.de       #Humboldt University Berlin
141.20.1.65                     #commodus.cms.hu-berlin.de
141.20.1.66                     #faustinus.cms.hu-berlin.de
141.20.1.67                     #marcellus.cms.hu-berlin.de
>ifh.de                 #DESY Zeuthen
141.34.22.10                    #romulus.ifh.de
141.34.22.11                    #remus.ifh.de
141.34.22.29                    #hekate.ifh.de
>integra-ev.de          #INTEGRA e.V.
134.155.48.8                    #afsdb2.integra-ev.de
134.155.48.63                   #afsdb1.integra-ev.de
>lrz-muenchen.de        #Leibniz Computing Centre, Germany
129.187.10.36                   #afs1.lrz-muenchen.de
129.187.10.56                   #afs3.lrz-muenchen.de
129.187.10.57                   #afs2.lrz-muenchen.de
>ipp-garching.mpg.de    #Institut fuer Plasmaphysik
130.183.9.5                     #afs-db1.rzg.mpg.de
130.183.14.14                   #afs-db3.bc.rzg.mpg.de
130.183.100.10                  #afs-db2.aug.ipp-garching.mpg.de
>mpe.mpg.de             #MPE cell
130.183.130.7                   #irafs1.mpe-garching.mpg.de
130.183.134.20                  #irafs2.mpe-garching.mpg.de
>nicsys.de              #NICsys GbR
213.187.80.3                    #attila.nicsys.de
>i1.informatik.rwth-aachen.de #Informatik I, RWTH Aachen
137.226.244.79                  #remus.informatik.rwth-aachen.de
>combi.tfh-wildau.de    #Philips Research Lab
194.95.50.106                   #joda13.combi.tfh-wildau.de
>tu-berlin.de           #TU Berlin
130.149.204.10                  #afsc-pr-1.tubit.tu-berlin.de
130.149.204.11                  #afsc-pr-2.tubit.tu-berlin.de
130.149.204.70                  #afsc-ba-1.tubit.tu-berlin.de
>tu-bs.de               #Technical University of Braunschweig, Germany
134.169.1.1                     #rzafs1.rz.tu-bs.de
134.169.1.5                     #rzafs2.rz.tu-bs.de
134.169.1.6                     #rzafs3.rz.tu-bs.de
>tu-chemnitz.de         #Technische Universitaet Chemnitz, Germany
134.109.2.1                     #zuse.hrz.tu-chemnitz.de
134.109.2.2                     #andrew.hrz.tu-chemnitz.de
134.109.2.15                    #phoenix.hrz.tu-chemnitz.de
>e18.ph.tum.de          #Experimental Physics, TU Munich, Germany
129.187.154.165                 #dionysos.e18.physik.tu-muenchen.de
129.187.154.223                 #hamlet.e18.physik.tu-muenchen.de
>physik.uni-bonn.de     #Institute of Physics, University of Bonn, Germany
131.220.166.33                  #afsdb1.physik.uni-bonn.de
131.220.166.34                  #afsdb2.physik.uni-bonn.de
131.220.166.35                  #afsdb3.physik.uni-bonn.de
>atlass01.physik.uni-bonn.de #Bonn ATLAS
131.220.165.43                  #atlass01.physik.uni-bonn.de
>uni-freiburg.de        #Albert-Ludwigs-Universitat Freiburg
132.230.6.237                   #sv8.ruf.uni-freiburg.de
132.230.6.239                   #sv10.ruf.uni-freiburg.de
>physik.uni-freiburg.de #Institute of Physics, University Freiburg, Germany
132.230.6.234                   #afs1.ruf.uni-freiburg.de
132.230.6.235                   #afs2.ruf.uni-freiburg.de
132.230.77.12                   #sherlock.physik.uni-freiburg.de
>math.uni-hamburg.de    #Department of Mathematics Uni Hamburg
134.100.223.3                   #afs-core.math.uni-hamburg.de
134.100.223.6                   #afs-core2.math.uni-hamburg.de
134.100.223.9                   #afs-core3.math.uni-hamburg.de
>physnet.uni-hamburg.de #PHYSnet-Rechenzentrum university of hamburg
134.100.106.44                  #afs-core.physnet.uni-hamburg.de
134.100.106.45                  #afs-core2.physnet.uni-hamburg.de
134.100.106.47                  #afs-core3.physnet.uni-hamburg.de
>iqo.uni-hannover.de    #Institut fuer Quantenoptik Hannover
130.75.103.221                  #afs1.iqo.uni-hannover.de
130.75.103.223                  #afs2.iqo.uni-hannover.de
>mathi.uni-heidelberg.de #Uni Heidelberg (Mathematisches Institut)
129.206.26.241                  #hactar.mathi.uni-heidelberg.de
>urz.uni-heidelberg.de  #Uni Heidelberg (Rechenzentrum)
129.206.119.10                  #afsdb.urz.uni-heidelberg.de
129.206.119.16                  #afsdb1.urz.uni-heidelberg.de
129.206.119.17                  #afsdb2.urz.uni-heidelberg.de
>ziti.uni-heidelberg.de #Institute of Computer Science at the University of Heidelberg
147.142.42.246                  #mp-sun.ziti.uni-heidelberg.de
147.142.42.252                  #mp-pizza.ziti.uni-heidelberg.de
>uni-hohenheim.de       #University of Hohenheim
144.41.2.2                      #rs13.serv.uni-hohenheim.de
144.41.2.3                      #rs14.serv.uni-hohenheim.de
144.41.2.4                      #rs15.serv.uni-hohenheim.de
>rz.uni-jena.de         #Rechenzentrum University of Jena, Germany
141.35.2.180                    #afs00.rz.uni-jena.de
141.35.2.181                    #afs01.rz.uni-jena.de
141.35.2.182                    #afs02.rz.uni-jena.de
>meteo.uni-koeln.de     #Univ. of Cologne - Inst. for Geophysics & Meteorology
134.95.144.22                   #afs1.meteo.uni-koeln.de
134.95.144.24                   #afs2.meteo.uni-koeln.de
>rrz.uni-koeln.de       #University of Cologne - Reg Comp Center
134.95.19.3                     #afsdb1.rrz.uni-koeln.de
134.95.19.4                     #afsdb2.rrz.uni-koeln.de
134.95.19.10                    #lyra.rrz.uni-koeln.de
134.95.67.97                    #afs.thp.uni-koeln.de
134.95.112.8                    #ladon.rrz.uni-koeln.de
>urz.uni-magdeburg.de   #Otto-von-Guericke-Universitaet, Magdeburg
141.44.7.6                      #lem.urz.uni-magdeburg.de
141.44.8.14                     #bowles.urz.uni-magdeburg.de
141.44.13.5                     #strugazki.urz.uni-magdeburg.de
>physik.uni-mainz.de    #institute of physics, university Mainz, Germany
134.93.130.93                   #hardy.physik.uni-mainz.de
>uni-mannheim.de        #Uni Mannheim (Rechenzentrum)
134.155.97.204                  #afsdb1.uni-mannheim.de
134.155.97.205                  #afsdb2.uni-mannheim.de
134.155.97.206                  #afsdb3.uni-mannheim.de
>mathematik.uni-stuttgart.de #University of Stuttgart, Math Dept.
129.69.61.1                     #fbm01.mathematik.uni-stuttgart.de
129.69.61.2                     #fbm02.mathematik.uni-stuttgart.de
129.69.61.3                     #fbm03.mathematik.uni-stuttgart.de
>stud.mathematik.uni-stuttgart.de #CIP-Pool of Math. Dept, University of Stuttgart
129.69.61.28                    #omni.mathematik.uni-stuttgart.de
129.69.116.201                  #stud01.mathematik.uni-stuttgart.de
129.69.116.202                  #stud02.mathematik.uni-stuttgart.de
129.69.116.203                  #stud03.mathematik.uni-stuttgart.de
>physik.uni-wuppertal.de #Physics department of Bergische Universität Wuppertal
132.195.104.3                   #afs1.physik.uni-wuppertal.de
132.195.104.230                 #afs2.physik.uni-wuppertal.de
>s-et.aau.dk            #Aalborg Univ., The Student Society, Denmark
130.225.196.22                  #afs.s-et.aau.dk
>ies.auc.dk             #Aalborg Univ., Inst. of Electronic Systems, Denmark
130.225.51.73                   #afsdb1.kom.auc.dk
130.225.51.74                   #afsdb2.kom.auc.dk
130.225.51.85                   #afsdb3.kom.auc.dk
>asu.edu                #Arizona State University
129.219.10.69                   #authen2.asu.edu
129.219.10.70                   #authen1.asu.edu
129.219.10.72                   #authen3.asu.edu
>hep.caltech.edu        #Caltech High Energy Physics
131.215.116.20                  #afs.hep.caltech.edu
>ugcs.caltech.edu       #Caltech UGCS lab
131.215.176.65                  #afs-c.ugcs.caltech.edu
131.215.176.67                  #afs-a.ugcs.caltech.edu
131.215.176.68                  #afs-b.ugcs.caltech.edu
>andrew.cmu.edu         #Carnegie Mellon University - Computing Services Cell
128.2.10.2                      #afsdb-01.andrew.cmu.edu
128.2.10.7                      #afsdb-02.andrew.cmu.edu
128.2.10.11                     #afsdb-03.andrew.cmu.edu
>mw.andrew.cmu.edu      #Carnegie Mellon University - Middleware Test Cell
128.2.234.24                    #null.andrew.cmu.edu
128.2.234.170                   #mw-mgr.andrew.cmu.edu
>club.cc.cmu.edu        #Carnegie Mellon University Computer Club
128.2.204.149                   #barium.club.cc.cmu.edu
128.237.157.11                  #sodium.club.cc.cmu.edu
128.237.157.13                  #potassium.club.cc.cmu.edu
>chem.cmu.edu           #Carnegie Mellon University - Chemistry Dept.
128.2.40.134                    #afs.chem.cmu.edu
128.2.40.140                    #afs2.chem.cmu.edu
>cs.cmu.edu             #Carnegie Mellon University - School of Comp. Sci.
128.2.172.58                    #date.srv.cs.cmu.edu
128.2.172.60                    #fig.srv.cs.cmu.edu
128.2.200.97                    #watermelon.srv.cs.cmu.edu
>ece.cmu.edu            #Carnegie Mellon University - Elec. Comp. Eng. Dept.
128.2.129.7                     #porok.ece.cmu.edu
128.2.129.8                     #vicio.ece.cmu.edu
128.2.129.9                     #e-xing.ece.cmu.edu
>scotch.ece.cmu.edu     #CMU ECE CALCM research group
128.2.134.82                    #lagavulin.ece.cmu.edu
>qatar.cmu.edu          #Carnegie Mellon University - Qatar
86.36.46.6                      #afs1.qatar.cmu.edu
86.36.46.7                      #afs2.qatar.cmu.edu
>sbp.ri.cmu.edu         #Carnegie Mellon University - Sensor Based Planning Lab
128.2.179.12                    #nihao.sbp.ri.cmu.edu
128.2.179.113                   #youtheman.sbp.ri.cmu.edu
>cnf.cornell.edu        #CNF
128.253.198.9                   #hole.cnf.cornell.edu
128.253.198.27                  #smoke.cnf.cornell.edu
128.253.198.231                 #mist.cnf.cornell.edu
>math.cornell.edu       #Cornell Math Dept
128.84.234.12                   #pooh.math.cornell.edu
128.84.234.16                   #bernoulli.math.cornell.edu
128.84.234.162                  #dyno.math.cornell.edu
>msc.cornell.edu        #Cornell University Materials Science Center
128.84.231.242                  #miranda.ccmr.cornell.edu
128.84.241.35                   #co.ccmr.cornell.edu
128.84.249.78                   #dax.ccmr.cornell.edu
>dbic.dartmouth.edu     #Dartmouth Brain Imaging Center
129.170.30.143                  #dbicafs1.dartmouth.edu
129.170.30.144                  #dbicafs2.dartmouth.edu
129.170.30.145                  #dbicafs3.dartmouth.edu
>northstar.dartmouth.edu #Dartmouth College Research Computing
129.170.16.22                   #halley.dartmouth.edu
129.170.16.26                   #andromeda.dartmouth.edu
129.170.16.43                   #cygnusx1.dartmouth.edu
>cs.hm.edu              #Department Computer Science Munich University Of Applied Science
129.187.208.31                  #afs1.cs.hm.edu
>eecs.harvard.edu       #Harvard - EECS
140.247.60.64                   #lefkada.eecs.harvard.edu
140.247.60.83                   #corfu.eecs.harvard.edu
>iastate.edu            #Iowa State University
129.186.1.243                   #afsdb-1.iastate.edu
129.186.6.243                   #afsdb-2.iastate.edu
129.186.142.243                 #afsdb-3.iastate.edu
>athena.mit.edu         #MIT/Athena cell
18.3.48.11                      #aether.mit.edu
18.9.48.11                      #castor.mit.edu
18.9.48.12                      #pollux.mit.edu
>csail.mit.edu          #MIT Computer Science & Artificial Intelligence Lab
128.30.2.13                     #titanic.csail.mit.edu
128.30.2.31                     #vasa.csail.mit.edu
128.30.2.75                     #maine.csail.mit.edu
>lns.mit.edu            #MIT/LNS Cell
198.125.160.134                 #afs2.lns.mit.edu.
198.125.160.217                 #afsdbserv1.lns.mit.edu.
198.125.160.218                 #afsdbserv2.lns.mit.edu.
>net.mit.edu            #MIT/Network Group cell
18.7.62.60                      #willy.mit.edu
18.9.48.15                      #moby.mit.edu
18.9.48.16                      #springer.mit.edu
>numenor.mit.edu        #Project Numenor
18.243.2.49                     #numenor.mit.edu
>sipb.mit.edu           #MIT/SIPB cell
18.181.0.19                     #reynelda.mit.edu
18.181.0.22                     #rosebud.mit.edu
18.181.0.23                     #ronald-ann.mit.edu
>msu.edu                #Michigan State University Main Cell
35.9.7.10                       #afsdb0.cl.msu.edu
>nd.edu                 #University of Notre Dame
129.74.223.17                   #john.helios.nd.edu
129.74.223.33                   #lizardo.helios.nd.edu
129.74.223.65                   #buckaroo.helios.nd.edu
>crc.nd.edu             #University of Notre Dame - Center for Research Computing
129.74.85.34                    #afsdb1.crc.nd.edu
129.74.85.35                    #afsdb2.crc.nd.edu
129.74.85.36                    #afsdb3.crc.nd.edu
>pitt.edu               #University of Pittsburgh
136.142.8.15                    #afs09.srv.cis.pitt.edu
136.142.8.20                    #afs10.srv.cis.pitt.edu
136.142.8.21                    #afs11.srv.cis.pitt.edu
>cs.pitt.edu            #University of Pittsburgh - Computer Science
136.142.22.5                    #afs01.cs.pitt.edu
136.142.22.6                    #afs02.cs.pitt.edu
136.142.22.7                    #afs03.cs.pitt.edu
>psc.edu                #PSC (Pittsburgh Supercomputing Center)
128.182.59.182                  #shaggy.psc.edu
128.182.66.184                  #velma.psc.edu
128.182.66.185                  #daphne.psc.edu
>scoobydoo.psc.edu      #PSC Test Cell
128.182.59.181                  #scooby.psc.edu
>cede.psu.edu           #Penn State - Center for Engr. Design & Entrepeneurship
146.186.218.10                  #greenly.cede.psu.edu
146.186.218.60                  #b50.cede.psu.edu
146.186.218.246                 #stalin.cede.psu.edu
>rose-hulman.edu        #Rose-Hulman Institute of Technology
137.112.7.11                    #afs1.rose-hulman.edu
137.112.7.12                    #afs2.rose-hulman.edu
137.112.7.13                    #afs3.rose-hulman.edu
>cs.rose-hulman.edu     #Rose-Hulman CS Department
137.112.40.10                   #galaxy.cs.rose-hulman.edu
>rpi.edu                #Rensselaer Polytechnic Institute
128.113.22.11                   #saul.server.rpi.edu
128.113.22.12                   #joab.server.rpi.edu
128.113.22.13                   #korah.server.rpi.edu
128.113.22.14                   #achan.server.rpi.edu
>hep.sc.edu             #University of South Carolina, Dept. of Physics
129.252.78.77                   #cpeven.physics.sc.edu
>cs.stanford.edu        #Stanford University Computer Science Department
171.64.64.10                    #cs-afs-1.Stanford.EDU
171.64.64.66                    #cs-afs-2.stanford.edu
171.64.64.69                    #cs-afs-3.stanford.edu
>ir.stanford.edu        #Stanford University
171.64.7.222                    #afsdb1.stanford.edu
171.64.7.234                    #afsdb2.stanford.edu
171.64.7.246                    #afsdb3.stanford.edu
>slac.stanford.edu      #Stanford Linear Accelerator Center
134.79.18.25                    #afsdb1.slac.stanford.edu
134.79.18.26                    #afsdb2.slac.stanford.edu
134.79.18.27                    #afsdb3.slac.stanford.edu
>physics.ucsb.edu       #UC Santa Barbara, Physics Dept
128.111.18.161                  #ledzeppelin.physics.ucsb.edu
>cats.ucsc.edu          #University of California, Santa Cruz
128.114.123.8                   #afs-prod-front-1.ucsc.edu
128.114.123.9                   #afs-prod-front-2.ucsc.edu
128.114.123.10                  #afs-prod-front-3.ucsc.edu
>ncsa.uiuc.edu          #National Center for Supercomputing Applications at Illinois
141.142.192.66                  #nile-vm.ncsa.uiuc.edu
141.142.192.143                 #congo-vm.ncsa.uiuc.edu
141.142.192.144                 #kaskaskia-vm.ncsa.uiuc.edu
>umbc.edu               #Universityf Maryland, Baltimore County
130.85.24.23                    #db2.afs.umbc.edu
130.85.24.87                    #db3.afs.umbc.edu
130.85.24.101                   #db1.afs.umbc.edu
>glue.umd.edu           #University of Maryland - Project Glue
128.8.70.11                     #olmec.umd.edu
128.8.236.4                     #egypt.umd.edu
128.8.236.230                   #babylon.umd.edu
>wam.umd.edu            #University of Maryland Network WAM Project
128.8.70.9                      #csc-srv.wam.umd.edu
128.8.236.5                     #avw-srv.wam.umd.edu
128.8.236.231                   #ptx-srv.wam.umd.edu
>umich.edu              #University of Michigan - Campus
141.211.1.32                    #fear.ifs.umich.edu
141.211.1.33                    #surprise.ifs.umich.edu
141.211.1.34                    #ruthless.ifs.umich.edu
>atlas.umich.edu        #ATLAS group cell in physics at University of Michigan
141.211.43.102                  #linat02.grid.umich.edu
141.211.43.103                  #linat03.grid.umich.edu
141.211.43.104                  #linat04.grid.umich.edu
>citi.umich.edu         #University of Michigan - Center for Information Technology Integ
141.212.112.5                   #babylon.citi.umich.edu
>isis.unc.edu           #Univ. of NC at Chapel Hill - ITS
152.2.1.5                       #db0.isis.unc.edu
152.2.1.6                       #db1.isis.unc.edu
152.2.1.7                       #db2.isis.unc.edu
>eng.utah.edu           #University of Utah - Engineering
155.98.111.9                    #lenny.eng.utah.edu
155.98.111.10                   #carl.eng.utah.edu
>cs.uwm.edu             #University of Wisconsin--Milwaukee
129.89.38.124                   #solomons.cs.uwm.edu
129.89.143.71                   #filip.cs.uwm.edu
>cs.wisc.edu            #Univ. of Wisconsin-Madison, Computer Sciences Dept.
128.105.132.14                  #timon.cs.wisc.edu
128.105.132.15                  #pumbaa.cs.wisc.edu
128.105.132.16                  #zazu.cs.wisc.edu
>engr.wisc.edu          #University of Wisconsin-Madison, College of Engineering
144.92.13.14                    #larry.cae.wisc.edu
144.92.13.15                    #curly.cae.wisc.edu
144.92.13.16                    #moe.cae.wisc.edu
>hep.wisc.edu           #University of Wisconsin -- High Energy Physics
128.104.28.219                  #anise.hep.wisc.edu
144.92.180.7                    #rosemary.hep.wisc.edu
144.92.180.30                   #fennel.hep.wisc.edu
>physics.wisc.edu       #Univ. of Wisconsin-Madison, Physics Department
128.104.160.13                  #kendra.physics.wisc.edu
128.104.160.14                  #fray.physics.wisc.edu
128.104.160.15                  #buffy.physics.wisc.edu
>ciemat.es              #Ciemat, Madrid, Spain
130.206.11.42                   #afsdb1.ciemat.es
130.206.11.217                  #afsdb2.ciemat.es
130.206.11.247                  #afsdb3.ciemat.es
>ifca.unican.es         #Instituto de Fisica de Cantabria (IFCA), Santander, Spain
193.144.209.20                  #gridwall.ifca.unican.es
>ific.uv.es             #Instituto de Fisica Corpuscular, Valencia, Spain
147.156.163.11                  #alpha.ific.uv.es
>alteholz.eu            #alteholz.eu
78.47.192.125                   #krb1eu.afs.alteholz.net
>in2p3.fr               #IN2P3
134.158.104.11                  #ccafsdb01.in2p3.fr
134.158.104.12                  #ccafsdb02.in2p3.fr
134.158.104.13                  #ccafsdb03.in2p3.fr
>mcc.ac.gb              #University of Manchester
130.88.203.41                   #nevis.mc.man.ac.uk
130.88.203.144                  #eryri.mc.man.ac.uk
130.88.203.145                  #scafell.mc.man.ac.uk
>anl.gov                #Argonne National Laboratory
146.137.96.33                   #arteus.it.anl.gov
146.137.162.88                  #agamemnon.it.anl.gov
146.137.194.80                  #antenor.it.anl.gov
>rhic.bnl.gov           #Relativistic Heavy Ion Collider
130.199.6.51                    #rafs03.rcf.bnl.gov
130.199.6.52                    #rafs02.rcf.bnl.gov
130.199.6.69                    #rafs01.rcf.bnl.gov
>usatlas.bnl.gov        #US Atlas Tier 1 Facility at BNL
130.199.48.32                   #aafs01.usatlas.bnl.gov
130.199.48.33                   #aafs02.usatlas.bnl.gov
130.199.48.34                   #aafs03.usatlas.bnl.gov
>fnal.gov               #Fermi National Acclerator Laboratory
131.225.68.1                    #fsus01.fnal.gov
131.225.68.4                    #fsus03.fnal.gov
131.225.68.14                   #fsus04.fnal.gov
>jpl.nasa.gov           #Jet Propulsion Laboratory
137.78.160.21                   #afsdb08.jpl.nasa.gov
137.78.160.22                   #afsdb09.jpl.nasa.gov
137.78.160.23                   #afsdb10.jpl.nasa.gov
>doe.atomki.hu          #Institute of Nuclear Research (MTA ATOMKI), Debrecen, Hungary
193.6.179.31                    #afs.doe.atomki.hu
>bme.hu                 #Budapest University of Technology and Economics
152.66.241.6                    #afs.iit.bme.hu
>kfki.hu                #Wigner Research Centre for Physics - Budapest, Hungary
148.6.2.109                     #afs0.kfki.hu
>rnd.ru.is              #Reykjavik University Research and Development Network
130.208.242.66                  #lithium.rnd.ru.is.
130.208.242.67                  #beryllium.rnd.ru.is.
130.208.242.68                  #boron.rnd.ru.is.
>caspur.it              #CASPUR Inter-University Computing Consortium, Rome
193.204.5.45                    #pomodoro.caspur.it
193.204.5.46                    #banana.caspur.it
193.204.5.50                    #maslo.caspur.it
>enea.it                #ENEA New Tech. Energy & Environment Agency, Italy
192.107.54.5                    #aixfs.frascati.enea.it
192.107.54.11                   #rs2ced.frascati.enea.it
192.107.54.12                   #43p.frascati.enea.it
>fusione.it             #Assoz. FUSIONE/Euratom, ENEA, Frascati-Italy
192.107.90.2                    #fusafs1.frascati.enea.it
192.107.90.3                    #fusafs2.frascati.enea.it
192.107.90.4                    #fusafs3.frascati.enea.it
>icemb.it               #ICEMB, Universita' La Sapienza - Rome - Italy
193.204.6.130                   #icembfs.caspur.it
>ictp.it                #The Abdus Salam International Centre for Theoretical Physics (IC
140.105.34.7                    #afsdb1.ictp.it
140.105.34.8                    #afsdb2.ictp.it
>infn.it                #Istituto Nazionale di Fisica Nucleare (INFN), Italia
131.154.1.7                     #afscnaf.infn.it
141.108.26.75                   #afsrm1.roma1.infn.it
192.84.134.75                   #afsna.na.infn.it
>ba.infn.it             #INFN, Sezione di Bari
193.206.185.235                 #baafsserver.ba.infn.it
193.206.185.236                 #debsrv.ba.infn.it
>kloe.infn.it           #INFN, KLOE experiment at Laboratori di Frascati
192.135.25.111                  #kloeafs1.lnf.infn.it
192.135.25.112                  #kloeafs2.lnf.infn.it
>le.infn.it             #INFN, Sezione di Lecce
192.84.152.40                   #afs01.le.infn.it
192.84.152.148                  #afs02.le.infn.it
>lnf.infn.it            #INFN, Laboratori Nazionali di Frascati
193.206.84.121                  #afs1.lnf.infn.it
193.206.84.122                  #afs2.lnf.infn.it
193.206.84.123                  #afs3.lnf.infn.it
>lngs.infn.it           #INFN, Laboratori Nazionali del Gran Sasso
192.84.135.21                   #afs1.lngs.infn.it
192.84.135.133                  #afs2.lngs.infn.it
>pi.infn.it             #INFN, Sezione di Pisa
192.84.133.50                   #aix1.pi.infn.it
212.189.152.6                   #afs1.pi.infn.it
212.189.152.7                   #afs2.pi.infn.it
>roma3.infn.it          #Istituto Nazionale di Fisica Nucleare (INFN), Italia
193.205.159.17                  #afsrm3.roma3.infn.it
>psm.it                 #Progetto San Marco, Universita' di Roma-1
151.100.1.65                    #atlante.psm.uniroma1.it
>tgrid.it               #CASPUR-CILEA-CINECA Grid Cell
193.204.5.33                    #cccgrid.caspur.it
>math.unifi.it          #math.unifi.it
150.217.34.182                  #xeno.math.unifi.it
>ing.uniroma1.it        #Universita' La Sapienza, Fac. Ingeneria
151.100.85.253                  #alfa.ing.uniroma1.it
>dia.uniroma3.it        #University Roma Tre - DIA
193.204.161.67                  #srv.dia.uniroma3.it
193.204.161.79                  #aux.dia.uniroma3.it
193.204.161.118                 #afs.dia.uniroma3.it
>vn.uniroma3.it         #University Roma Tre, area Vasca Navale
193.205.219.59                  #alfa2.dia.uniroma3.it
193.205.219.60                  #beta2.dia.uniroma3.it
193.205.219.61                  #gamma2.dia.uniroma3.it
>italia                 #Italian public AFS cell
193.204.5.9                     #afs.caspur.it
>cmf.nrl.navy.mil       #Naval Research Laboratory - Center for Computational Science
134.207.12.68                   #picard.cmf.nrl.navy.mil
134.207.12.69                   #riker.cmf.nrl.navy.mil
134.207.12.70                   #kirk.cmf.nrl.navy.mil
>lcp.nrl.navy.mil       #Naval Research Lab - Lab for Computational Physics
132.250.114.2                   #afs1.lcp.nrl.navy.mil
132.250.114.4                   #afs2.lcp.nrl.navy.mil
132.250.114.6                   #afs3.lcp.nrl.navy.mil
>nucleares.unam.mx      #Instituto de Ciencias Nucleares, UNAM, Mexico
132.248.29.50                   #nahualli.nucleares.unam.mx
>crossproduct.net       #crossproduct.net
207.114.88.173                  #geodesic.crossproduct.net
>epitech.net            #EPITECH, France
163.5.255.41                    #afs-db-1.epitech.net
163.5.255.42                    #afs-db-2.epitech.net
>es.net                 #Energy Sciences Net
198.128.3.21                    #fs1.es.net
198.128.3.22                    #fs2.es.net
198.128.3.23                    #fs3.es.net
>gorlaeus.net           #Gorlaeus Laboratories, Leiden University
132.229.170.27                  #fwncisafs1.gorlaeus.net
>laroia.net             #Laroia Networks
66.66.102.254                   #supercore.laroia.net
>sinenomine.net         #Sine Nomine Associates
199.167.73.142                  #afsdb1.sinenomine.net
199.167.73.152                  #afsdb4.sinenomine.net
199.167.73.153                  #afsdb5.sinenomine.net
>slackers.net           #The Slackers' Network
199.4.150.159                   #alexandria.slackers.net
>tproa.net              #The People's Republic of Ames
204.11.35.83                    #service-3.tproa.net
204.11.35.84                    #service-4.tproa.net
204.11.35.85                    #service-5.tproa.net
>interdose.net          #Interdose Ltd. & Co. KG, Germany
80.190.171.42                   #bfd9000.tow5.interdose.net
80.190.171.43                   #bfd9001.tow5.interdose.net
>nikhef.nl              #The Dutch National Institute for High Energy Physics
192.16.185.26                   #afs1.nikhef.nl
192.16.185.27                   #afs2.nikhef.nl
>acm-csuf.org           #California State Univerisity Fullerton ACM
137.151.29.193                  #afs1.acm-csuf.org
>adrake.org             #adrake.org
128.2.98.241                    #afs.adrake.org
>bazquux.org            #Baz Quux Organization
66.207.142.196                  #baxquux.org
>coed.org               #Adam Pennington's Cell
66.93.61.184                    #vice1.coed.org
128.237.157.35                  #vice3.coed.org
>dementia.org           #Dementia Unlimited (old)
128.2.13.209                    #dedlock.dementix.org
128.2.234.204                   #vorkana.dementix.org
128.2.235.26                    #meredith.dementix.org
>dementix.org           #Dementia Unlimited
128.2.13.209                    #dedlock.dementix.org
128.2.234.204                   #vorkana.dementix.org
128.2.235.26                    #meredith.dementix.org
>idahofuturetruck.org   #University of Idaho hybrid vehicle development
12.18.238.210                   #dsle210.fsr.net
>afs.ietfng.org         #ietfng.org
67.62.51.95                     #a.afs.ietfng.org
>jeaton.org             #jeaton.org (Jeffrey Eaton, jeaton@jeaton.org)
128.2.234.89                    #jeaton-org-01.jeaton.org
128.2.234.92                    #jeaton-org-02.jeaton.org
>mrph.org               #Mrph
66.207.133.1                    #sanber.mrph.org
128.2.99.209                    #hernandarias.m>mstacm.org             #Missouri Science & Technology - ACM
131.151.249.193                 #acm.mst.edu
>nomh.org               #nomh.org
204.29.154.12                   #iota.nomh.org
204.29.154.32                   #adversity.xi.nomh.org
>oc7.org                #The OC7 Project
128.2.122.140                   #knife.oc7.org
207.22.77.170                   #spoon.oc7.org
>pfriedma.org           #pfriedma.org
72.95.215.18                    #vice.pfriedma.org
>riscpkg.org            #The RISC OS Packaging Project
83.104.175.10                   #delenn.riscpkg.org
>kth.se                 #Royal Institute of Technology, Stockholm, Sweden
130.237.32.145                  #sonen.e.kth.se
130.237.48.7                    #anden.e.kth.se
130.237.48.244                  #fadern.e.kth.se
>ict.kth.se             #Royal Institute of Technology, Information and Communication tec
130.237.216.11                  #afsdb1.ict.kth.se
130.237.216.12                  #afsdb2.ict.kth.se
130.237.216.13                  #afsdb3.ict.kth.se
>isk.kth.se             #Royal Institute of Technology, ISK
130.237.216.17                  #afsdb1.isk.kth.se
130.237.216.82                  #afsdb2.isk.kth.se
130.237.216.83                  #afsdb3.isk.kth.se
>it.kth.se              #Royal Institute of Technology, Teleinformatics, Kista
130.237.216.14                  #afsdb1.it.kth.se
130.237.216.15                  #afsdb2.it.kth.se
130.237.216.16                  #afsdb3.it.kth.se
>md.kth.se              #Royal Institute of Technology, MMK
130.237.57.7                    #mdafs-1.md.kth.se
>mech.kth.se            #Royal Institute of Technology, MECH
130.237.233.142                 #matterhorn.mech.kth.se
130.237.233.143                 #castor.mech.kth.se
130.237.233.144                 #pollux.mech.kth.se
>nada.kth.se            #Royal Institute of Technology, NADA
130.237.222.20                  #kosmos.nada.kth.se
130.237.223.12                  #sputnik.nada.kth.se
130.237.224.78                  #mir.nada.kth.se
130.237.227.23                  #gagarin.nada.kth.se
130.237.228.28                  #laika.nada.kth.se
>pdc.kth.se             #Royal Institute of Technology, PDC
130.237.232.29                  #crab.pdc.kth.se
130.237.232.112                 #anna.pdc.kth.se
130.237.232.114                 #hokkigai.pdc.kth.se
>stacken.kth.se         #Stacken Computer Club
130.237.234.3                   #milko.stacken.kth.se
130.237.234.43                  #hot.stacken.kth.se
130.237.234.101                 #fishburger.stacken.kth.se
>syd.kth.se             #Royal Institute of Technology, KTH-Syd
130.237.83.23                   #afs.haninge.kth.se
>physto.se              #Physics department Stockholm University
130.237.205.36                  #sysafs1.physto.se
130.237.205.72                  #sysafs2.physto.se
>sanchin.se             #Sanchin Consulting AB, Sweden
192.195.148.10                  #sesan.sanchin.se
>su.se                  #Stockholm University
130.237.162.81                  #afsdb1.su.se
130.237.162.82                  #afsdb2.su.se
130.237.162.230                 #afsdb3.su.se
>f9.ijs.si              #F9, Jozef Stefan Institue
194.249.156.1                   #brenta.ijs.si
>p-ng.si                #University of Nova Gorica
193.2.120.2                     #solkan.p-ng.si
193.2.120.9                     #sabotin.p-ng.si
>ihep.su                #Institute for High-Energy Physics
194.190.165.201                 #fs0001.ihep.su
194.190.165.202                 #fs0002.ihep.su
>hep-ex.physics.metu.edu.tr #METU Department of Physics, Experimental HEP group, Ankara/Turke
144.122.31.131                  #neutrino.physics.metu.edu.tr
>phy.bris.ac.uk         #Bristol University - physics
137.222.74.18                   #zen.phy.bris.ac.uk
>inf.ed.ac.uk           #School of Informatics, University of Edinburgh
129.215.64.16                   #afsdb0.inf.ed.ac.uk
129.215.64.17                   #afsdb1.inf.ed.ac.uk
129.215.64.18                   #afsdb2.inf.ed.ac.uk
>phas.gla.ac.uk         #Univeristy of Glasgow Physics And Astronomy
194.36.1.19                     #afsdb1.phas.gla.ac.uk
194.36.1.27                     #afsdb3.phas.gla.ac.uk
194.36.1.33                     #afsdb2.phas.gla.ac.uk
>ic.ac.uk               #Imperial College London
155.198.63.148                  #icafs2.cc.ic.ac.uk
155.198.63.149                  #icafs1.cc.ic.ac.uk
>hep.man.ac.uk          #Manchester HEP
194.36.2.3                      #afs1.hep.man.ac.uk
194.36.2.4                      #afs2.hep.man.ac.uk
194.36.2.5                      #afs3.hep.man.ac.uk
>tlabs.ac.za            #iThemba LABS Cell
196.24.232.1                    #afs01.tlabs.ac.za
196.24.232.2                    #afs02.tlabs.ac.za
196.24.232.3                    #afs03.tlabs.ac.za
""" % (cell_name, cell_name, cell_ip, cell_name)
    template_helper.write_template_file(cellservdb_content, cellservdb_client_file_path, check_output=not skip_check_output)
    # OpenAFS setup
    try:
        cellservdb_server_file_parent_path = os.path.dirname(cellservdb_server_file_path)
        if not os.path.exists(cellservdb_server_file_parent_path):
            os.makedirs(cellservdb_server_file_parent_path)
        template_helper.write_template_file(cellservdb_content, cellservdb_server_file_path, check_output=not skip_check_output)
        # kerberos setup
        newrealm_proc = __pexpect_spawn__([kdb5_util, "create", "-s"])   #newrealm_cmds)
        newrealm_proc.expect(["Enter KDC database master key:"])
        newrealm_proc.sendline(krb_pw)
        newrealm_proc.expect(["Re-enter KDC database master key to verify:"])
        newrealm_proc.sendline(krb_pw)
        newrealm_proc.expect([pexpect.EOF])

        #__sp_check_call__([kdb5_util, "create", "-s"]) # otherwise `kadmin.local` fails with `kadmind: No such file or directory while initializing, aborting`

        admin_princ_name = "admin" # could be admin/admin as well
        afs_princ_name = "afs" # @TODO: check if afs/richtercloud.de causes trouble
        # add admins to ACL file
        logger.info("Adding admins to database") # use default encryption for `admin`
        template_helper.write_template_file("%s x" % (admin_princ_name,), krb_acl_file_path, check_output=not skip_check_output) # x means all permissions (see http://www.mit.edu/~kerberos/krb5-latest/doc/admin/conf_files/kadm5_acl.html#kadm5-acl-5 for details)
        logger.info("Starting the Kerberos daemons on the master KDC")
        if krb_path_mode == KRB_PATH_MODE_SOURCE:
            def __krb5kdc__():
                sp.check_call([krb5kdc]) # multiple starts don't cause trouble
            def __kadmind__():
                sp.check_call([kadmind]) # multiple starts don't cause trouble
            krb5kdc_thread = threading.Thread(target=__krb5kdc__)
            kadmind_thread = threading.Thread(target=__kadmind__)
            krb5kdc_thread.start()
            kadmind_thread.start()
        elif krb_path_mode == KRB_PATH_MODE_UBUNTU:
            __sp_check_call__([service, "krb5-admin-server", "restart"], no_fail=no_fail)
            __sp_check_call__([service, "krb5-kdc", "restart"], no_fail=no_fail)
        kadmin_proc = __pexpect_spawn__([kadmin_local])
        kadmin_proc.expect(["kadmin.local:"])
        kadmin_proc.sendline("addprinc %s@%s" % (admin_princ_name, cell_name))
        kadmin_proc.sendline(krb_pw)
        kadmin_proc.expect([":"])
        kadmin_proc.sendline(krb_pw)
        kadmin_proc.sendline("quit")
        kadmin_proc.expect(pexpect.EOF)
        logger.info("Testing authentication with kinit")
        kinit_proc = __pexpect_spawn__([kinit, "%s@%s" % (admin_princ_name, cell_name,)])
        kinit_proc.sendline(krb_pw)
        kinit_proc.expect(pexpect.EOF)
        kinit_proc.close(force=False)
        kinit_proc_returncode = kinit_proc.exitstatus
        if kinit_proc_returncode != 0:
            raise RuntimeError("kinit authentication test failed (returned with code %d)" % (kinit_proc_returncode,))
        logger.info("Creating principals %s and %s" % (admin_princ_name, afs_princ_name,))
        keytab_encryption_option = ""
        if keytab_file_encryption != None:
            keytab_encryption_option = "-e %s" % (keytab_file_encryption,)
        kadmin_proc = __pexpect_spawn__([kadmin_local])
        kadmin_proc.expect(["kadmin.local:"])
        kadmin_proc.sendline("addprinc -randkey %s %s/%s" % (keytab_encryption_option, afs_princ_name, cell_name))
        logger.info("Exporting principal %s to keytab" % (afs_princ_name,)) # admin isn't export
        kadmin_proc.sendline("ktadd -k %s %s %s/%s" % (keytab_file_path, keytab_encryption_option, afs_princ_name, cell_name))
        kadmin_proc.sendline("quit")
        kadmin_proc.expect(pexpect.EOF)
        
        # export keys to keytab
        kvno_output = sp.check_output([kvno, "-k", keytab_file_path, "%s/%s" % (afs_princ_name, cell_name)]) # `kvno`'s `-e` argument refers to a `converting etype`
        kvno_keyno_match = re.search("kvno = (?P<no>[0-9]+)", kvno_output.strip())
        try:
            kvno_keyno = kvno_keyno_match.group("no")
            logger.info("key number/kvno is %s" % (kvno_keyno,))
        except IndexError:
            raise RuntimeError("The kvno output '%s' didn't contain a 'kvno = [number]' section" % (kvno_output.strip(),))
        # display keys in keytab for information
        sp.check_call([klist, "-e", "-k", keytab_file_path])
        __sp_check_call__([asetkey, "add",
            #"rxkad_krb5",
            kvno_keyno,
            #"17", # encryption type according to IANA registry (see OpenAFS quick start guide p. 28 for details, "most common numbers are 18 for aes256-cts-hmac-sha1-96 and 17 for aes128-cts-hmac-sha1-96" (ib.))
            keytab_file_path,
            "%s/%s@%s" % (afs_princ_name, cell_name, cell_name),
        ], no_fail=no_fail) # OpenAFS quick start guide suggests `asetkey add rxkad_krb5 <kvno> 18 /usr/afs/etc/rxkad.keytab afs/<cell name>` which doesn't work (fails with returncode 1)
        __sp_check_call__([asetkey, "add",
            #"rxkad_krb5",
            kvno_keyno,
            #"18", # encryption type according to IANA registry (see OpenAFS quick start guide p. 28 for details, "most common numbers are 18 for aes256-cts-hmac-sha1-96 and 17 for aes128-cts-hmac-sha1-96" (ib.))
            keytab_file_path,
            "%s/%s@%s" % (afs_princ_name, cell_name, cell_name),
        ], no_fail=no_fail)

        # start bosserver
        if path_mode == PATH_MODE_UBUNTU:
            __sp_check_call__([service, "openafs-fileserver", "restart"], no_fail=no_fail)
            __sp_check_call__([service, "openafs-client", "restart"], no_fail=no_fail)
        else:
            __restart_bosserver__()
        # set cellname
        __sp_check_call__([bos, "setcellname", machine_name,
            cell_name,
            "-localauth"])
        #.check_output([bos, "listhosts", machine_name, "-localauth"]) # fails with `bos: failed to set cell (could not find entry)`, but shouldn't -> skip temporarily
        # create buserver, ptserver, vlserver
        # @TODO: check whether servers are already created rather than using try-except blocks
        __sp_check_call__([bos, "create", machine_name, "buserver", "simple", buserver, "-localauth"], no_fail=no_fail)
        __sp_check_call__([bos, "create", machine_name, "ptserver", "simple", ptserver, "-localauth"], no_fail=no_fail)
        __sp_check_call__([bos, "create", machine_name, "vlserver", "simple", vlserver, "-localauth"], no_fail=no_fail)
        if path_mode == PATH_MODE_UBUNTU:
            __sp_check_call__([service, "openafs-fileserver", "restart"], no_fail=no_fail)
            __sp_check_call__([service, "openafs-client", "restart"], no_fail=no_fail)
        else:
            __restart_bosserver__()

        __sp_check_call__([bos, "adduser", machine_name, "admin", "-localauth"], no_fail=no_fail)
        
        # Initializing the Protection Database
        __sp_check_call__([pts, "createuser", "-name", "admin", "-cell", cell_name, "-localauth"], no_fail=no_fail)
        __sp_check_call__([pts, "adduser", "-user", "admin", "-group", "system:administrators", "-localauth"], no_fail=no_fail)
        # check membership correct
        __sp_check_call__([pts, "membership", "admin", "-localauth"])
        __sp_check_call__([bos, "restart", machine_name, "-all", "-localauth"])
        # use Demand-Attach File-Server (DAFS) because it promises better performance<ref>http://wiki.openafs.org/DemandAttach/</ref> and doesn't seem to require more configuration or maintenance than the default fileserver
        __sp_check_call__([bos, "create", machine_name, "dafs", "dafs", dafileserver, davolserver, salvageserver, dasalvager, "-localauth"], no_fail=no_fail)
        # check server up and running
        __sp_check_call__([bos, "status", machine_name, "dafs", "-long", "-localauth"], no_fail=no_fail)
        __sp_check_call__([bos, "status", machine_name, "dafs", "-long", "-localauth"], no_fail=no_fail)
        if not upgrade:
            __sp_check_call__([vos, "create", machine_name,
                "/vicepa", # partition name
                "root.afs", "-localauth"], no_fail=no_fail)
        else:
            __sp_check_call__([vos, "syncvldb", machine_name, "-verbose", "-localauth"], no_fail=no_fail)
            __sp_check_call__([vos, "syncserv", machine_name, "-verbose", "-localauth"], no_fail=no_fail)
        # Starting the Server Portion of the Update Server
        __sp_check_call__([bos, "create", machine_name, "upserver", "simple", upserver,
            # "-crypt", os.path.dirname(keytab_file_path), # no longer recognized
            # "-clear", "/usr/local/libexec/openafs", # no longer recognized
            "-localauth"], no_fail=no_fail)
        logger.info("eventually configure NTPD (if you mistrust the system provided service)")
        # Configuring the client (on the first AFS machine)
        #shutil.copy(thiscell_server_file_path, thiscell_client_file_path)
        #shutil.copy(cellservdb_server_file_path, cellservdb_client_file_path)
        template_helper.write_template_file("""/afs:%s:50000
""" % (cache_dir_path,), cacheinfo_file_path, check_output=not skip_check_output)
        if not os.path.exists(cache_dir_path):
            os.makedirs(cache_dir_path)
        elif os.path.isfile(cache_dir_path):
            raise ValueError("cache directory '%s' is a file" % (cache_dir_path,))
    finally:
        if bosserver_proc:
            if bosserver_proc.poll() is None:
                bosserver_proc.send_signal(signal.SIGINT)
                bosserver_proc_try_time = 0
                bosserver_proc_try_max = 5
                bosserver_proc_try_interval = 0.1
                while bosserver_proc_try_time < bosserver_proc_try_max and bosserver_proc.poll is None:
                    bosserver_proc_try_time += bosserver_proc_try_interval
                    time.sleep(bosserver_proc_try_interval)
                bosserver_proc.terminate()

def __bosserver__():
    while bosserver_proc.poll() is None:
        time.sleep(0.5)
    bosserver_proc_returncode = bosserver_proc.poll()
    if bosserver_proc_returncode != 0:
        raise RuntimeError("x process returned non-zero code %d" % (bosserver_proc_returncode,))

def __restart_bosserver__():
    if bosserver_proc != None:
        bosserver_proc.terminate()
    bosserver_proc = __sp_popen__([bosserver,
        #"-noauth" # deprecated and replaced though -localauth added to caller commands
    ])
    bosserver_thread = threading.Thread(target=__bosserver__)
    bosserver_thread.start()

def main():
    """setuptools entry_point"""
    plac.call(openafs_setup)

if __name__ == "__main__":
    main()
