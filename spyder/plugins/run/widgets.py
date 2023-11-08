# -*- coding: utf-8 -*-
#
# Copyright © Spyder Project Contributors
# Licensed under the terms of the MIT License
# (see spyder/__init__.py for details)

"""Run dialogs and widgets and data models."""

# Standard library imports
import os.path as osp
from typing import Optional, Tuple, List, Dict
from uuid import uuid4

# Third party imports
from qtpy.compat import getexistingdirectory
from qtpy.QtCore import QSize, Qt, Signal
from qtpy.QtGui import QFontMetrics
from qtpy.QtWidgets import (
    QApplication, QDialog, QDialogButtonBox, QGridLayout, QGroupBox,
    QHBoxLayout, QLabel, QLineEdit, QLayout, QRadioButton, QStackedWidget,
    QVBoxLayout, QWidget)
import qstylizer.style

# Local imports
from spyder.api.translations import _
from spyder.api.config.fonts import SpyderFontType, SpyderFontsMixin
from spyder.api.widgets.comboboxes import SpyderComboBox
from spyder.plugins.run.api import (
    RunParameterFlags, WorkingDirSource, WorkingDirOpts,
    RunExecutionParameters, ExtendedRunExecutionParameters,
    RunExecutorConfigurationGroup, SupportedExecutionRunConfiguration)
from spyder.utils.icon_manager import ima
from spyder.utils.misc import getcwd_or_home
from spyder.utils.palette import QStylePalette
from spyder.utils.qthelpers import create_toolbutton
from spyder.utils.stylesheet import AppStyle
from spyder.widgets.collapsible import CollapsibleWidget


# Main constants
FILE_DIR = _("The directory of the configuration being executed")
CW_DIR = _("The current working directory")
FIXED_DIR = _("The following directory:")

class RunDialogStatus:
    Close = 0
    Save = 1
    Run = 2


class BaseRunConfigDialog(QDialog):
    """Run configuration dialog box, base widget"""
    size_change = Signal(QSize)

    def __init__(self, parent=None, disable_run_btn=False):
        QDialog.__init__(self, parent)
        self.setWindowFlags(
            self.windowFlags() & ~Qt.WindowContextHelpButtonHint)

        # Destroying the C++ object right after closing the dialog box,
        # otherwise it may be garbage-collected in another QThread
        # (e.g. the editor's analysis thread in Spyder), thus leading to
        # a segmentation fault on UNIX or an application crash on Windows
        self.setAttribute(Qt.WA_DeleteOnClose)

        self.setWindowIcon(ima.icon('run_settings'))
        layout = QVBoxLayout()
        self.setLayout(layout)
        self.disable_run_btn = disable_run_btn

    def add_widgets(self, *widgets_or_spacings):
        """Add widgets/spacing to dialog vertical layout"""
        layout = self.layout()
        for widget_or_spacing in widgets_or_spacings:
            if isinstance(widget_or_spacing, int):
                layout.addSpacing(widget_or_spacing)
            elif isinstance(widget_or_spacing, QLayout):
                layout.addLayout(widget_or_spacing)
            else:
                layout.addWidget(widget_or_spacing)
        return layout

    def add_button_box(self, stdbtns):
        """Create dialog button box and add it to the dialog layout"""
        self.bbox = QDialogButtonBox(stdbtns)

        if not self.disable_run_btn:
            run_btn = self.bbox.addButton(
                _("Run"), QDialogButtonBox.ActionRole)
            run_btn.clicked.connect(self.run_btn_clicked)

        reset_deafults_btn = self.bbox.addButton(
            _('Reset'), QDialogButtonBox.ResetRole)
        reset_deafults_btn.clicked.connect(self.reset_btn_clicked)
        self.bbox.accepted.connect(self.accept)
        self.bbox.rejected.connect(self.reject)

        btnlayout = QHBoxLayout()
        btnlayout.addStretch(1)
        btnlayout.addWidget(self.bbox)
        self.layout().addLayout(btnlayout)

    def resizeEvent(self, event):
        """
        Reimplement Qt method to be able to save the widget's size from the
        main application
        """
        QDialog.resizeEvent(self, event)
        self.size_change.emit(self.size())

    def run_btn_clicked(self):
        """Run button was just clicked"""
        pass

    def reset_btn_clicked(self):
        """Reset button was clicked."""
        pass

    def ok_btn_clicked(self):
        """Ok button was clicked."""
        pass

    def setup(self):
        """Setup Run Configuration dialog with filename *fname*"""
        raise NotImplementedError


class ExecutionParametersDialog(BaseRunConfigDialog):
    """Run execution parameters edition dialog."""

    def __init__(
        self,
        parent,
        executor_name,
        executor_params: Dict[Tuple[str, str], SupportedExecutionRunConfiguration],
        extensions: Optional[List[str]] = None,
        contexts: Optional[Dict[str, List[str]]] = None,
        default_params: Optional[ExtendedRunExecutionParameters] = None,
        extension: Optional[str] = None,
        context: Optional[str] = None,
        new_config: bool = False
    ):
        super().__init__(parent, True)

        self.executor_name = executor_name
        self.executor_params = executor_params
        self.default_params = default_params
        self.extensions = extensions or []
        self.contexts = contexts or {}
        self.extension = extension
        self.context = context
        self.new_config = new_config

        self.parameters_name = None
        if default_params is not None:
            self.parameters_name = default_params['name']

        self.current_widget = None
        self.status = RunDialogStatus.Close
        self.saved_conf = None

    # ---- Public methods
    # -------------------------------------------------------------------------
    def setup(self):
        # Widgets
        ext_combo_label = QLabel(_("Select a file extension:"))
        context_combo_label = QLabel(_("Select a run context:"))

        self.extension_combo = SpyderComboBox(self)
        self.extension_combo.currentIndexChanged.connect(
            self.extension_changed)

        self.context_combo = SpyderComboBox(self)
        self.context_combo.currentIndexChanged.connect(self.context_changed)

        self.extension_combo.setMinimumWidth(150)
        self.context_combo.setMinimumWidth(150)

        ext_context_g_layout = QGridLayout()
        ext_context_g_layout.addWidget(ext_combo_label, 0, 0)
        ext_context_g_layout.addWidget(self.extension_combo, 0, 1)
        ext_context_g_layout.addWidget(context_combo_label, 1, 0)
        ext_context_g_layout.addWidget(self.context_combo, 1, 1)

        ext_context_layout = QHBoxLayout()
        ext_context_layout.addLayout(ext_context_g_layout)
        ext_context_layout.addStretch(1)

        self.stack = QStackedWidget()
        self.executor_group = QGroupBox(_("Executor parameters"))
        executor_layout = QVBoxLayout(self.executor_group)
        executor_layout.addWidget(self.stack)

        self.wdir_group = QGroupBox(_("Working directory settings"))

        wdir_layout = QVBoxLayout(self.wdir_group)

        self.file_dir_radio = QRadioButton(FILE_DIR)
        wdir_layout.addWidget(self.file_dir_radio)

        self.cwd_radio = QRadioButton(CW_DIR)
        wdir_layout.addWidget(self.cwd_radio)

        fixed_dir_layout = QHBoxLayout()
        self.fixed_dir_radio = QRadioButton(FIXED_DIR)
        fixed_dir_layout.addWidget(self.fixed_dir_radio)

        self.wd_edit = QLineEdit(self)
        self.fixed_dir_radio.toggled.connect(self.wd_edit.setEnabled)
        self.wd_edit.setEnabled(False)
        fixed_dir_layout.addWidget(self.wd_edit)
        browse_btn = create_toolbutton(
            self,
            triggered=self.select_directory,
            icon=ima.icon('DirOpenIcon'),
            tip=_("Select directory")
        )
        fixed_dir_layout.addWidget(browse_btn)
        wdir_layout.addLayout(fixed_dir_layout)

        if self.new_config:
            params_name_text = _("Save configuration as:")
        else:
            params_name_text = _("Configuration name:")

        params_name_label = QLabel(params_name_text)
        self.store_params_text = QLineEdit(self)
        self.store_params_text.setMinimumWidth(300)
        store_params_layout = QHBoxLayout()
        store_params_layout.addWidget(params_name_label)
        store_params_layout.addWidget(self.store_params_text)
        store_params_layout.addStretch(1)

        all_group = QVBoxLayout()
        all_group.addWidget(self.executor_group)
        all_group.addWidget(self.wdir_group)

        # Final layout
        layout = self.add_widgets(
            store_params_layout,
            15,
            ext_context_layout,
            10,
            all_group
        )

        widget_dialog = QWidget(self)
        widget_dialog.setMinimumWidth(600)
        widget_dialog.setLayout(layout)
        scroll_layout = QVBoxLayout(self)
        scroll_layout.addWidget(widget_dialog)
        self.add_button_box(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)

        # Set title
        self.setWindowTitle(
            _("New run configuration for: {}").format(self.executor_name)
        )

        self.extension_combo.addItems(self.extensions)

        extension_index = 0
        if self.extension is not None:
            extension_index = self.extensions.index(self.extension)
            self.extension_combo.setEnabled(False)

        if self.context is not None:
            self.context_combo.setEnabled(False)

        self.extension_combo.setCurrentIndex(extension_index)

        if self.parameters_name:
            self.store_params_text.setText(self.parameters_name)

    def extension_changed(self, index: int):
        if index < 0:
            return

        self.selected_extension = self.extension_combo.itemText(index)
        contexts = self.contexts[self.selected_extension]

        self.context_combo.clear()
        self.context_combo.addItems(contexts)
        self.context_combo.setCurrentIndex(-1)

        context_index = 0
        if self.context is not None:
            context_index = contexts.index(self.context)
        self.context_combo.setCurrentIndex(context_index)

    def context_changed(self, index: int):
        if index < 0:
            return

        # Clear the QStackWidget contents
        self.current_widget = None
        while self.stack.count() > 0:
            widget = self.stack.widget(0)
            self.stack.removeWidget(widget)

        self.selected_context = self.context_combo.itemText(index)

        executor_conf_metadata = self.executor_params[
            (self.selected_extension, self.selected_context)]

        requires_cwd = executor_conf_metadata['requires_cwd']
        ConfigWidget = (executor_conf_metadata['configuration_widget'] or
                        RunExecutorConfigurationGroup)

        if executor_conf_metadata['configuration_widget'] is None:
            self.executor_group.setEnabled(False)
        else:
            self.executor_group.setEnabled(True)

        self.wdir_group.setEnabled(requires_cwd)

        self.current_widget = ConfigWidget(
            self, self.selected_context, self.selected_extension, {})
        self.stack.addWidget(self.current_widget)

        working_dir_params = WorkingDirOpts(
            source=WorkingDirSource.ConfigurationDirectory,
            path=None)
        exec_params = RunExecutionParameters(
            working_dir=working_dir_params,
            executor_params=None)

        default_params = self.current_widget.get_default_configuration()

        if self.default_params:
            params = self.default_params['params']
            working_dir_params = params['working_dir']
            exec_params = params

        params_set = exec_params['executor_params'] or default_params

        if params_set.keys() == default_params.keys():
            self.current_widget.set_configuration(params_set)

        source = working_dir_params['source']
        path = working_dir_params['path']

        if source == WorkingDirSource.ConfigurationDirectory:
            self.file_dir_radio.setChecked(True)
            self.cwd_radio.setChecked(False)
            self.fixed_dir_radio.setChecked(False)
            self.wd_edit.setText('')
        elif source == WorkingDirSource.CurrentDirectory:
            self.file_dir_radio.setChecked(False)
            self.cwd_radio.setChecked(True)
            self.fixed_dir_radio.setChecked(False)
            self.wd_edit.setText('')
        elif source == WorkingDirSource.CustomDirectory:
            self.file_dir_radio.setChecked(False)
            self.cwd_radio.setChecked(False)
            self.fixed_dir_radio.setChecked(True)
            self.wd_edit.setText(path)

        if (not self.executor_group.isEnabled() and not
                self.wdir_group.isEnabled()):
            ok_btn = self.bbox.button(QDialogButtonBox.Ok)
            ok_btn.setEnabled(False)

        self.adjustSize()

    def select_directory(self):
        """Select directory"""
        basedir = str(self.wd_edit.text())
        if not osp.isdir(basedir):
            basedir = getcwd_or_home()
        directory = getexistingdirectory(self, _("Select directory"), basedir)
        if directory:
            self.wd_edit.setText(directory)
            self.dir = directory

    def reset_btn_clicked(self):
        index = self.context_combo.currentIndex()
        self.context_changed(index)

    def run_btn_clicked(self):
        self.status |= RunDialogStatus.Run

    def ok_btn_clicked(self):
        self.status |= RunDialogStatus.Save

    def get_configuration(
            self
    ) -> Tuple[str, str, ExtendedRunExecutionParameters]:

        return self.saved_conf

    # ---- Qt methods
    # -------------------------------------------------------------------------
    def accept(self) -> None:
        self.status |= RunDialogStatus.Save
        widget_conf = self.current_widget.get_configuration()

        path = None
        source = None
        if self.file_dir_radio.isChecked():
            source = WorkingDirSource.ConfigurationDirectory
        elif self.cwd_radio.isChecked():
            source = WorkingDirSource.CurrentDirectory
        else:
            source = WorkingDirSource.CustomDirectory
            path = self.wd_edit.text()

        cwd_opts = WorkingDirOpts(source=source, path=path)

        exec_params = RunExecutionParameters(
            working_dir=cwd_opts, executor_params=widget_conf)

        if self.default_params:
            uuid = self.default_params['uuid']
        else:
            uuid = str(uuid4())

        name = self.store_params_text.text()
        if name == '':
            self.store_params_text.setPlaceholderText(
                _("Set a name here to proceed!")
            )
            return

        ext_exec_params = ExtendedRunExecutionParameters(
            uuid=uuid,
            name=name,
            params=exec_params,
            file_uuid=None
        )

        self.saved_conf = (self.selected_extension, self.selected_context,
                           ext_exec_params)

        super().accept()


class RunDialog(BaseRunConfigDialog, SpyderFontsMixin):
    """Run dialog used to configure run executors."""

    def __init__(
        self,
        parent=None,
        run_conf_model=None,
        executors_model=None,
        parameter_model=None,
        disable_run_btn=False
    ):
        super().__init__(parent, disable_run_btn=disable_run_btn)

        self.run_conf_model = run_conf_model
        self.executors_model = executors_model
        self.parameter_model = parameter_model

        self.current_widget = None
        self.status = RunDialogStatus.Close
        self._is_shown = False

    # ---- Public methods
    # -------------------------------------------------------------------------
    def setup(self):
        # --- Header
        self.header_label = QLabel(self)
        self.header_label.setObjectName("run-header-label")

        # --- File combobox
        # It's hidden by default to decrease the complexity of this dialog
        self.configuration_combo = SpyderComboBox(self)
        self.configuration_combo.hide()

        # --- Executor and parameters widgets
        executor_label = QLabel(_("Run this file in:"))
        self.executor_combo = SpyderComboBox(self)
        parameters_label = QLabel(_("Preset configuration:"))
        self.parameters_combo = SpyderComboBox(self)

        self.executor_combo.setMinimumWidth(250)
        self.parameters_combo.setMinimumWidth(250)

        executor_g_layout = QGridLayout()
        executor_g_layout.addWidget(executor_label, 0, 0)
        executor_g_layout.addWidget(self.executor_combo, 0, 1)
        executor_g_layout.addWidget(parameters_label, 1, 0)
        executor_g_layout.addWidget(self.parameters_combo, 1, 1)

        executor_layout = QHBoxLayout()
        executor_layout.addLayout(executor_g_layout)
        executor_layout.addStretch()

        # --- Configuration properties
        config_props_group = QGroupBox(_("Configuration properties"))
        config_props_layout = QVBoxLayout(config_props_group)

        # Increase margin between title and line edit below so this looks good
        config_props_margins = config_props_layout.contentsMargins()
        config_props_margins.setTop(12)
        config_props_layout.setContentsMargins(config_props_margins)

        # Name to save custom configuration
        name_params_label = QLabel(_("Name:"))
        self.name_params_text = QLineEdit(self)
        self.name_params_text.setMinimumWidth(250)

        name_params_layout = QHBoxLayout()
        name_params_layout.addWidget(name_params_label)
        name_params_layout.addWidget(self.name_params_text)
        name_params_layout.addStretch()
        config_props_layout.addLayout(name_params_layout)

        # --- Runner settings
        self.stack = QStackedWidget()
        self.executor_group = QGroupBox(_("Runner settings"))
        self.executor_group.setObjectName("run-executor-group")

        parameters_layout = QVBoxLayout(self.executor_group)
        parameters_layout.addWidget(self.stack)

        # Remove bottom margin because it adds unnecessary space
        parameters_layout_margins = parameters_layout.contentsMargins()
        parameters_layout_margins.setBottom(0)
        parameters_layout.setContentsMargins(parameters_layout_margins)

        # --- Working directory settings
        self.wdir_group = QGroupBox(_("Working directory settings"))
        self.wdir_group.setObjectName("run-wdir-group")
        wdir_layout = QVBoxLayout(self.wdir_group)

        self.file_dir_radio = QRadioButton(FILE_DIR)
        wdir_layout.addWidget(self.file_dir_radio)

        self.cwd_radio = QRadioButton(CW_DIR)
        wdir_layout.addWidget(self.cwd_radio)

        fixed_dir_layout = QHBoxLayout()
        self.fixed_dir_radio = QRadioButton(FIXED_DIR)
        fixed_dir_layout.addWidget(self.fixed_dir_radio)
        self.wd_edit = QLineEdit(self)
        self.fixed_dir_radio.toggled.connect(self.wd_edit.setEnabled)
        self.wd_edit.setEnabled(False)
        fixed_dir_layout.addWidget(self.wd_edit)
        browse_btn = create_toolbutton(
            self,
            triggered=self.select_directory,
            icon=ima.icon('DirOpenIcon'),
            tip=_("Select directory")
        )
        fixed_dir_layout.addWidget(browse_btn)
        wdir_layout.addLayout(fixed_dir_layout)

        # --- Group all customization widgets into a collapsible one
        custom_config = CollapsibleWidget(self, _("Custom configuration"))
        custom_config.addWidget(config_props_group)
        custom_config.addWidget(self.executor_group)
        custom_config.addWidget(self.wdir_group)

        # Remove unnecessary margin at the bottom.
        custom_config.set_content_bottom_margin(0)

        # Center dialog after custom_config is expanded/collapsed
        custom_config._animation.finished.connect(self._center_dialog)

        # --- Final layout
        layout = self.add_widgets(
            self.header_label,
            self.configuration_combo,  # Hidden for simplicity
            executor_layout,
            custom_config
        )

        widget_dialog = QWidget(self)
        widget_dialog.setMinimumWidth(600)
        widget_dialog.setLayout(layout)
        scroll_layout = QVBoxLayout(self)
        scroll_layout.addWidget(widget_dialog)
        self.add_button_box(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)

        # --- Settings
        self.executor_combo.currentIndexChanged.connect(
            self.display_executor_configuration)
        self.executor_combo.setModel(self.executors_model)

        self.configuration_combo.currentIndexChanged.connect(
            self.update_configuration_run_index)
        self.configuration_combo.setModel(self.run_conf_model)
        self.configuration_combo.setCurrentIndex(
            self.run_conf_model.get_initial_index())
        self.configuration_combo.setMaxVisibleItems(1)

        self.executor_combo.setMaxVisibleItems(20)
        self.executor_combo.view().setVerticalScrollBarPolicy(
            Qt.ScrollBarAsNeeded)

        self.parameters_combo.currentIndexChanged.connect(
            self.update_parameter_set)
        self.parameters_combo.setModel(self.parameter_model)

        self.setWindowTitle(_("Run configuration per file"))
        self.layout().setSizeConstraint(QLayout.SetFixedSize)

        self.setStyleSheet(self._stylesheet)

    def select_directory(self):
        """Select directory"""
        basedir = str(self.wd_edit.text())
        if not osp.isdir(basedir):
            basedir = getcwd_or_home()
        directory = getexistingdirectory(self, _("Select directory"), basedir)
        if directory:
            self.wd_edit.setText(directory)
            self.dir = directory

    def update_configuration_run_index(self, index: int):
        self.executor_combo.setCurrentIndex(-1)
        self.run_conf_model.update_index(index)
        self.executor_combo.setCurrentIndex(
            self.executors_model.get_initial_index())

    def update_parameter_set(self, index: int):
        if index < 0:
            return

        if self.index_to_select is not None:
            index = self.index_to_select
            self.index_to_select = None
            self.parameters_combo.setCurrentIndex(index)

        action, params = self.parameter_model.get_executor_parameters(index)
        working_dir_params = params['working_dir']
        stored_parameters = params['executor_params']

        if action == RunParameterFlags.SetDefaults:
            stored_parameters = self.current_widget.get_default_configuration()
        self.current_widget.set_configuration(stored_parameters)

        source = working_dir_params['source']
        path = working_dir_params['path']

        if source == WorkingDirSource.ConfigurationDirectory:
            self.file_dir_radio.setChecked(True)
            self.cwd_radio.setChecked(False)
            self.fixed_dir_radio.setChecked(False)
            self.wd_edit.setText('')
        elif source == WorkingDirSource.CurrentDirectory:
            self.file_dir_radio.setChecked(False)
            self.cwd_radio.setChecked(True)
            self.fixed_dir_radio.setChecked(False)
            self.wd_edit.setText('')
        elif source == WorkingDirSource.CustomDirectory:
            self.file_dir_radio.setChecked(False)
            self.cwd_radio.setChecked(False)
            self.fixed_dir_radio.setChecked(True)
            self.wd_edit.setText(path)

    def display_executor_configuration(self, index: int):
        if index == -1:
            return

        # Clear the QStackWidget contents
        self.current_widget = None
        while self.stack.count() > 0:
            widget = self.stack.widget(0)
            self.stack.removeWidget(widget)

        exec_tuple = self.executors_model.get_selected_run_executor(index)
        executor_name, executor_info = exec_tuple
        enable_cwd = executor_info['requires_cwd']
        self.wdir_group.setEnabled(enable_cwd)

        ConfigWidget = (executor_info['configuration_widget'] or
                        RunExecutorConfigurationGroup)

        if executor_info['configuration_widget'] is None:
            self.executor_group.setVisible(False)
        else:
            self.executor_group.setVisible(True)

        metadata = self.run_conf_model.get_selected_metadata()
        context = metadata['context']
        input_extension = metadata['input_extension']
        uuid = metadata['uuid']

        self.current_widget = ConfigWidget(
            self, context, input_extension, metadata)
        self.stack.addWidget(self.current_widget)

        if uuid not in self.run_conf_model:
            return

        stored_params = self.run_conf_model.get_run_configuration_parameters(
            uuid, executor_name)['params']

        # Only show global parameters (i.e. those with file_uuid = None) or
        # those that correspond to the current file.
        stored_params = {
            k:v for (k, v) in stored_params.items()
            if v.get("file_uuid") in [None, uuid]
        }

        self.parameter_model.set_parameters(stored_params)
        selected_params = self.run_conf_model.get_last_used_execution_params(
            uuid, executor_name)
        index = self.parameter_model.get_parameters_index_by_uuid(
            selected_params
        )

        if self.parameters_combo.count() == 0:
            self.index_to_select = index

        self.parameters_combo.setCurrentIndex(index)
        self.adjustSize()

    def select_executor(self, executor_name: str):
        self.executor_combo.setCurrentIndex(
            self.executors_model.get_run_executor_index(executor_name))

    def reset_btn_clicked(self):
        self.parameters_combo.setCurrentIndex(-1)
        index = self.executor_combo.currentIndex()
        self.display_executor_configuration(index)
        self.name_params_text.setText('')

    def run_btn_clicked(self):
        self.status |= RunDialogStatus.Run
        self.accept()

    def get_configuration(
        self
    ) -> Tuple[str, str, ExtendedRunExecutionParameters, bool]:

        return self.saved_conf

    # ---- Qt methods
    # -------------------------------------------------------------------------
    def accept(self) -> None:
        self.status |= RunDialogStatus.Save

        default_conf = self.current_widget.get_default_configuration()
        widget_conf = self.current_widget.get_configuration()

        # Check if config is named
        given_name = self.name_params_text.text()
        if not given_name and widget_conf != default_conf:
            # If parameters are not named and are different from the default
            # ones, we always save them in a config named "Custom". This avoids
            # the hassle of asking users to provide a name when they want to
            # customize the config.
            given_name = _("Custom")

        # Get index associated with config
        if given_name:
            idx = self.parameter_model.get_parameters_index_by_name(given_name)
        else:
            idx = self.parameters_combo.currentIndex()

        # Get uuid and name from index
        if idx == -1:
            # This means that there are no saved parameters for given_name, so
            # we need to generate a new uuid for them.
            uuid = str(uuid4())
            name = given_name
        else:
            # Retrieve uuid and name from our config system
            uuid, name = self.parameter_model.get_parameters_uuid_name(idx)

        path = None
        source = None
        if self.file_dir_radio.isChecked():
            source = WorkingDirSource.ConfigurationDirectory
        elif self.cwd_radio.isChecked():
            source = WorkingDirSource.CurrentDirectory
        else:
            source = WorkingDirSource.CustomDirectory
            path = self.wd_edit.text()

        cwd_opts = WorkingDirOpts(source=source, path=path)

        exec_params = RunExecutionParameters(
            working_dir=cwd_opts, executor_params=widget_conf)

        metadata_info = self.run_conf_model.get_metadata(
            self.configuration_combo.currentIndex()
        )

        ext_exec_params = ExtendedRunExecutionParameters(
            uuid=uuid,
            name=name,
            params=exec_params,
            file_uuid=metadata_info['uuid']
        )

        executor_name, __ = self.executors_model.get_selected_run_executor(
            self.executor_combo.currentIndex()
        )

        self.saved_conf = (metadata_info['uuid'], executor_name,
                           ext_exec_params)

        return super().accept()

    def showEvent(self, event):
        """Adjustments when the widget is shown."""
        if not self._is_shown:
            # Set file name as the header
            fname = self.configuration_combo.currentText()
            header_font = (
                self.get_font(SpyderFontType.Interface, font_size_delta=1)
            )

            # Elide fname in case fname is too long
            fm = QFontMetrics(header_font)
            text = fm.elidedText(
                fname, Qt.ElideLeft, self.header_label.width()
            )

            self.header_label.setFont(header_font)
            self.header_label.setAlignment(Qt.AlignCenter)
            self.header_label.setText(text)
            if text != fname:
                self.header_label.setToolTip(fname)

            self._is_shown = True

        super().showEvent(event)

    # ---- Private methods
    # -------------------------------------------------------------------------
    @property
    def _stylesheet(self):
        css = qstylizer.style.StyleSheet()

        # --- Style for the header
        css["QLabel#run-header-label"].setValues(
            # Give it a background color to make it highlight over the other
            # widgets.
            backgroundColor=QStylePalette.COLOR_BACKGROUND_4,
            # The left and right margins are a bit bigger to prevent the file
            # name from being too close to the borders in case it's too long.
            padding=f"{2 * AppStyle.MarginSize} {4 * AppStyle.MarginSize}",
            borderRadius=QStylePalette.SIZE_BORDER_RADIUS,
            # Add good enough margin with the widgets below it.
            marginBottom=f"{AppStyle.InnerContentPadding}px"
        )


        # --- Style for the collapsible
        css["CollapsibleWidget"].setValues(
            # Separate it from the widgets above it with the same margin as the
            # one between the header and those widgets.
            marginTop=f"{AppStyle.InnerContentPadding}px"
        )

        # --- Style for QGroupBoxes
        # This makes the spacing between this group and the one above it (i.e.
        # "Configuration properties") to be almost the same as the one between
        # it and the group below (i.e. "Working directory settings").
        css["QGroupBox#run-executor-group::title"].setValues(
            marginTop="7px"
        )

        # Reduce extra top margin for this group to make the spacing between
        # groups uniform.
        css["QGroupBox#run-wdir-group::title"].setValues(
            marginTop="-5px"
        )

        return css.toString()

    def _center_dialog(self):
        """
        Center dialog relative to the main window after collapsing/expanding
        the custom configuration widget.
        """
        # main_window is usually not available in our tests, so we need to
        # check for this.
        main_window = getattr(QApplication.instance(), 'main_window', None)

        if main_window:
            x = (
                main_window.pos().x()
                + ((main_window.width() - self.width()) / 2)
            )

            y = (
                main_window.pos().y()
                + ((main_window.height() - self.height()) / 2)
            )

            self.move(x, y)
