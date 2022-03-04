from typing import Any, Optional, Union

from pymmcore_plus import CMMCorePlus, PropertyType
from qtpy.QtCore import Signal, Slot
from qtpy.QtWidgets import QCheckBox, QComboBox, QLabel, QLineEdit, QWidget
from superqt import QLabeledDoubleSlider, QLabeledSlider, utils

from .._core import get_core_singleton


class PropertyWidget(QWidget):
    """A widget that presents a view onto an mmcore device property.

    Parameters
    ----------
    device_label : str
        Device label
    prop_name : str
        Property name
    parent : Optional[QWidget]
        parent widget, by default None
    core : Optional[CMMCorePlus]
        Optional CMMCorePlus instance, by default the global singleton.

    Raises
    ------
    ValueError
        If the `device_label` is not loaded, or does not have a property `prop_name`.
    """

    valueChanged = Signal(object)

    def __init__(
        self,
        device_label: str,
        prop_name: str,
        *,
        parent: Optional[QWidget] = None,
        core: Optional[CMMCorePlus] = None,
    ) -> None:
        super().__init__(parent=parent)
        self._mmc = core or get_core_singleton()

        if device_label not in self._mmc.getLoadedDevices():
            raise ValueError(f"Device not loaded: {device_label!r}")

        if not self._mmc.hasProperty(device_label, prop_name):
            names = self._mmc.getDevicePropertyNames(device_label)
            raise ValueError(
                f"Device {device_label!r} has no property {prop_name!r}. "
                f"Availble property names include: {names}"
            )

        self._device_label = device_label
        self._prop_name = prop_name

        self._post_init()

        # calling after post_init so that subclasses can set range first.
        self.setValue(self._mmc.getProperty(device_label, prop_name))

        # connect events and queue for disconnection on widget destroyed
        self._mmc.events.propertyChanged.connect(self._on_core_change)
        self.destroyed.connect(self._disconnect)
        if not self.isReadOnly():
            self.valueChanged.connect(self._on_widget_change)

    def _on_core_change(self, dev: str, prop: str, new_val: str) -> None:
        if dev == self._device_label and prop == self._prop_name:
            with utils.signals_blocked(self):
                self.setValue(new_val)

    def _on_widget_change(self, value: Any) -> None:
        self._mmc.setProperty(self._device_label, self._prop_name, value)

    def _disconnect(self):
        self._mmc.events.propertyChanged.disconnect(self._on_core_change)

    def _post_init(self):
        """for subclasses"""

    def value(self) -> Any:
        """Return the current value of the *widget*."""
        raise NotImplementedError("abstract base class.")

    def setValue(self, value: Any) -> None:
        """Set the current value of the *widget*."""
        raise NotImplementedError("abstract base class.")

    def isReadOnly(self) -> bool:
        """Return True if the property is read only."""
        return self._mmc.isPropertyReadOnly(self._device_label, self._prop_name)

    def propertyType(self) -> PropertyType:
        """Return PropertyType of this property."""
        return self._mmc.getPropertyType(self._device_label, self._prop_name)

    def refresh(self) -> None:
        """Set the value of the widget to core.

        (If all goes well this shouldn't be necessary, but if a propertyChanged
        event is missed, this can be used).
        """
        val = self._mmc.getProperty(self._device_label, self._prop_name)
        with utils.signals_blocked(self):
            self.setValue(val)

    @classmethod
    def for_property(
        cls, device_label: str, prop_name: str, *, core: Optional[CMMCorePlus] = None
    ) -> "PropertyWidget":
        """Return a widget for device `dev`, property `prop`.

        The resulting widget will be used for PropertyWidget._value_widget.

        Parameters
        ----------
        device_label : str
            Device label
        prop_name : str
            Property name
        core : Optional[CMMCorePlus]
            Optional CMMCorePlus instance, by default the global singleton.

        Returns
        -------
        PPropValueWidget
            A widget with a normalized PropValueWidget protocol.
        """
        core = core or get_core_singleton()
        prop_type = core.getPropertyType(device_label, prop_name)

        # Create the widget based on property type and allowed choices
        if core.isPropertyReadOnly(device_label, prop_name):
            WdgCls = ReadOnlyWidget
        if allowed := core.getAllowedPropertyValues(device_label, prop_name):
            # note: many string properties are also choices between "Yes", "No"
            if prop_type is PropertyType.Integer and set(allowed) == {"0", "1"}:
                WdgCls = IntBoolWidget
            else:
                WdgCls = ChoiceWidget
        else:
            WdgCls = {
                PropertyType.Integer: IntegerWidget,
                PropertyType.Float: FloatWidget,
            }.get(prop_type, StringWidget)

        return WdgCls(device_label, prop_name, core=core)


def _set_lims(wdg: Union["IntegerWidget", "FloatWidget"]) -> None:
    dev, prop = wdg._device_label, wdg._prop_name
    if wdg._mmc.hasPropertyLimits(dev, prop):
        pytype = wdg.propertyType().to_python()
        wdg.setMinimum(pytype(wdg._mmc.getPropertyLowerLimit(dev, prop)))
        wdg.setMaximum(pytype(wdg._mmc.getPropertyUpperLimit(dev, prop)))


def _stretch_range_to_contain(wdg: QLabeledDoubleSlider, val: float) -> float:
    """Set range of `wdg` to include `val`."""
    if val > wdg.maximum():
        wdg.setMaximum(val)
    if val < wdg.minimum():
        wdg.setMinimum(val)
    return val


class IntegerWidget(PropertyWidget, QLabeledSlider):
    """Slider suited to managing integer values"""

    def setValue(self, v: int) -> None:
        return super().setValue(_stretch_range_to_contain(self, int(v)))

    def _post_init(self):
        _set_lims(self)


class FloatWidget(QLabeledDoubleSlider, PropertyWidget):
    """Slider suited to managing float values"""

    def setValue(self, v: float) -> None:
        return super().setValue(_stretch_range_to_contain(self, float(v)))

    def _post_init(self):
        _set_lims(self)


class IntBoolWidget(QCheckBox, PropertyWidget):
    """Checkbox for boolean values, which are integers in pymmcore"""

    valueChanged = Signal(int)

    def _post_init(self):
        self.toggled.connect(self._emit)

    @Slot(bool)
    def _emit(self, state):
        self.valueChanged.emit(int(state))

    def value(self) -> int:
        return int(self.isChecked())

    def setValue(self, val: Union[str, int]) -> None:
        return self.setChecked(bool(int(val)))


class ChoiceWidget(QComboBox, PropertyWidget):
    """Combobox for props with a set of allowed values."""

    valueChanged = Signal(str)

    def _post_init(self):
        self.currentTextChanged.connect(self.valueChanged.emit)
        self._allowed = self._mmc.getAllowedPropertyValues(
            self._device_label, self._prop_name
        )
        if self._allowed:
            self.addItems(self._allowed)

    def value(self) -> str:
        return self.currentText()

    def setValue(self, value: str) -> None:
        if value not in self._allowed:
            raise ValueError(f"{value!r} must be one of {self._allowed}")
        self.setCurrentText(str(value))


class StringWidget(PropertyWidget, QLineEdit):
    """String widget for pretty much everything else."""

    valueChanged = Signal(str)

    def _post_init(self) -> None:
        self.textChanged.connect(self.valueChanged.emit)

    def value(self) -> str:
        return self.text()

    def setValue(self, value: str) -> None:
        self.setText(str(value))


class ReadOnlyWidget(PropertyWidget, QLabel):
    """Read-only widget, cannot be edited."""

    def value(self) -> str:
        return self.text()

    def setValue(self, value: Any) -> None:
        self.setText(str(value))
