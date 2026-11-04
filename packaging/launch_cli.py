import sys

from fieldortho.app import _set_gis_data

_set_gis_data()

from fieldortho.cli import main  # noqa: E402

sys.exit(main())
