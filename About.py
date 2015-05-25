# -*- coding: utf-8 -*-
"""Contains details about the GDSC OMERO python scripts"""

import omero.scripts as scripts

if __name__ == "__main__":
    """
    The main entry point of the script, as called by the client via the
    scripting service, passing the required parameters.
    """
    client = scripts.client('About', """\
All the scripts in this directory have been developed by the 
Queensland Brain Institute at The University of Queensland.

See: http://web.qbi.uq.edu.au/microscopy/qbi-omero-scripts/""",
        version="1.0",
        authors=["Daniel Matthews", "QBI"],
        institutions=["University of Queensland"],
        contact="d.matthews1@uq.edu.au",
    )  # noqa
