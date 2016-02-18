# -*- coding: utf-8 -*-
"""Contains details about the GDSC OMERO python scripts"""
from omero.gateway import BlitzGateway
from omero.rtypes import rlong, rstring
import omero.scripts as scripts

if __name__ == "__main__":
    """
    The main entry point of the script, as called by the client via the
    scripting service, passing the required parameters.
    """
    client = scripts.client('About QBI scripts', """The scripts in this directory \

    have been developed by the Queensland Brain Institute at \

    The University of Queensland.

    Copyright(C) 2016 QBI

For information on their use please see:\
 http://web.qbi.uq.edu.au/microscopy/qbi-omero-scripts/""",
        version="1.0",
        authors=["QBI"],
        institutions=["The University of Queensland"],
        contact="qbi.microscopy@uq.edu.au",
    )
    try:
        conn = BlitzGateway(client_obj=client)
        # create a session on the server.
        client.createSession()
        # Do work here including calling functions
        # defined above.

        client.setOutput("Message", rstring("Success"))

    finally:
        client.closeSession()