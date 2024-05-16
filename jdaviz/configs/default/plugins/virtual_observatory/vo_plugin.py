from astropy.coordinates import SkyCoord
from astropy.io import fits
from astropy import units as u
from pyvo.utils import vocabularies
from pyvo import registry
from pyvo.dal.exceptions import DALFormatError
from requests.exceptions import ConnectionError as RequestConnectionError
from traitlets import Dict, Bool, Unicode, Any, List, Int

from jdaviz.core.events import SnackbarMessage
from jdaviz.core.registries import tray_registry
from jdaviz.core.template_mixin import PluginTemplateMixin, AddResultsMixin, TableMixin

__all__ = ['VoPlugin']


@tray_registry('VoPlugin', label="Virtual Observatory")
class VoPlugin(PluginTemplateMixin, AddResultsMixin, TableMixin):
    """ Plugin to query the Virtual Observatory and load data into Imviz """
    template_file = __file__, "vo_plugin.vue"

    wavebands = List().tag(sync=True)
    resources = List([]).tag(sync=True)
    resources_loading = Bool(False).tag(sync=True)

    source = Unicode().tag(sync=True)
    radius_deg = Int(1).tag(sync=True)

    results_loading = Bool(False).tag(sync=True)
    data_loading = Bool(False).tag(sync=True)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        # Waveband properties to filter available registry resources
        self.wavebands = [w.lower() for w in vocabularies.get_vocabulary("messenger")["terms"]]
        self.waveband_selected = None

        self._full_registry_results = None
        self.resource_selected = None

        self.table.headers_avail = ["Title", "Instrument", "DateObs", "URL"]
        self.table.headers_visible = ["Title", "Instrument", "DateObs"]
        self._populate_url_only = False

        self.table.show_rowselect = True
        self.table.item_key = "URL"

    def vue_waveband_selected(self,event):
        """ Sync waveband selected

        When the user selects a waveband, query Virtual Observatory registry
        for all SIA services that serve data in that waveband. Then update
        the dropdown accordingly.
        """
        self.waveband_selected = event
        # Clear existing resources list
        self.resources = []
        self.resource_selected = None
        self.resources_loading = True # Start loading bar
        try:
            if event is not None:
                self._full_registry_results = registry.search(registry.Servicetype("sia"), registry.Waveband(self.waveband_selected))
                self.resources = list(self._full_registry_results.getcolumn("short_name"))
        except DALFormatError as e:
            if type(e.cause) is RequestConnectionError:
                self.hub.broadcast(SnackbarMessage(
                        f"Unable to connect to VO registry. Please check your internet connection: {e}", sender=self, color="error"))
            else:
                raise e
        finally:
            self.resources_loading = False # Stop loading bar

    def vue_resource_selected(self, event):
        """Sync IVOA resource selected"""
        self.resource_selected = event

    def vue_query_resource(self, *args, **kwargs):
        """
        Once a specific VO resource is selected, query it with the user-specified source target.
        User input for source is first attempted to be parsed as a SkyCoord coordinate. If not,
        then attempts to parse as a target name.
        """
        # Reset Table
        self.table.items = []
        self._populate_url_only = False
        self.table.headers_visible = ["Title", "Instrument", "DateObs"]

        self.results_loading = True # Start loading spinner
        try:
            # Query SIA service
            # Service is indexed via short name (resource_selected), which is the suggested way
            # according to PyVO docs. Though disclaimer that collisions COULD occur. If so,
            # consider indexing on the full IVOID, which is guaranteed unique.
            sia_service = self._full_registry_results[self.resource_selected].get_service(service_type="sia")
            try:
                # First parse user-provided source as direct coordinates
                coord = SkyCoord(self.source, unit=u.deg)
            except:
                try:
                    # If that didn't work, try parsing it as an object name
                    coord = SkyCoord.from_name(self.source)
                except Exception:
                    raise LookupError(f"Unable to resolve source coordinates: {self.source}")

            # Once coordinate lookup is complete, search service using these coords.
            sia_results = sia_service.search(
                    coord,
                    size=((self.radius_deg * u.deg) if self.radius_deg > 0 else None),
                    format='image/fits')
            if len(sia_results) == 0:
                self.hub.broadcast(SnackbarMessage(
                    f"No observations returned at coords {coord} from VO SIA resource: {sia_service.baseurl}", sender=self, color="error"))
            else:
                self.hub.broadcast(SnackbarMessage(
                    f"{len(sia_results)} SIA results found!", sender=self, color="success"))
        except Exception as e:
            self.hub.broadcast(SnackbarMessage(
                f"Unable to locate files for source {self.source}: {e}", sender=self, color="error"))
            raise
        finally:
            self.results_loading = False # Stop loading spinner

        try:
            for result in sia_results:
                table_entry = {"URL": result.getdataurl()}
                if not self._populate_url_only:
                    try:
                        table_entry["Title"] = str(result.title)
                        table_entry["Instrument"] = str(result.instr)
                        table_entry["DateObs"] = str(result.dateobs)
                    except Exception as e:
                        self.hub.broadcast(SnackbarMessage(
                            f"Unable to get metadata columns. Switching table to URL-only: {e}", sender=self, color="warning"))
                        self.table.headers_visible = ["URL"]
                        self._populate_url_only = True
                self.table.add_item(table_entry)
            self.hub.broadcast(SnackbarMessage(
                    f"{len(sia_results)} SIA results populated!", sender=self, color="success"))
        except Exception as e:
            self.hub.broadcast(SnackbarMessage(
                f"Unable to populate table for source {self.source}: {e}", sender=self, color="error"))
            raise

    def vue_load_selected_data(self,event):
        """Load the files selected by the user in the table"""
        self.data_loading = True # Start loading spinner
        for entry in self.table.selected_rows:
            try:
                self.app._jdaviz_helper.load_data(
                    fits.open(str(entry["URL"])), # Open URL as FITS object
                    data_label=f"{self.source}_{self.resource_selected}_{entry.get('Title', entry.get('URL', ''))}")
            except Exception as e:
                self.hub.broadcast(SnackbarMessage(
                    f"Unable to load file to viewer: {entry['URL']}: {e}", sender=self, color="error"))
        # Clear selected entries' checkboxes on table
        self.table.selected_rows = []
        self.data_loading = False # Stop loading spinner
