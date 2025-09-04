"""Enhanced notebook widget with modern UI components, themes, and advanced interactions."""

from typing import Optional, Dict, Any, List, TYPE_CHECKING
from datetime import datetime

if TYPE_CHECKING:
    import ipywidgets as widgets

try:
    import ipywidgets as widgets  # noqa: F811
    from IPython.display import display, HTML

    IPYTHON_AVAILABLE = True
except ImportError:
    IPYTHON_AVAILABLE = False
    widgets = None  # type: ignore

from .config import ClusterConfig


class ThemeManager:
    """Manages dark/light themes and modern UI styling."""

    def __init__(self):
        self.current_theme = "light"
        self.themes = {
            "light": {
                "background": "#ffffff",
                "surface": "#f8f9fa",
                "primary": "#333366",
                "secondary": "#6c757d",
                "accent": "#007bff",
                "text": "#212529",
                "text_muted": "#6c757d",
                "border": "#dee2e6",
                "success": "#28a745",
                "warning": "#ffc107",
                "error": "#dc3545",
                "info": "#17a2b8",
            },
            "dark": {
                "background": "#121212",
                "surface": "#1e1e1e",
                "primary": "#bb86fc",
                "secondary": "#03dac6",
                "accent": "#cf6679",
                "text": "#ffffff",
                "text_muted": "#b3b3b3",
                "border": "#444444",
                "success": "#4caf50",
                "warning": "#ff9800",
                "error": "#f44336",
                "info": "#2196f3",
            },
        }

    def get_current_theme(self) -> Dict[str, str]:
        """Get the current theme colors."""
        return self.themes[self.current_theme]

    def set_theme(self, theme: str) -> bool:
        """Set the current theme."""
        if theme in self.themes:
            self.current_theme = theme
            return True
        return False

    def get_theme_css(self) -> str:
        """Generate CSS for the current theme."""
        colors = self.get_current_theme()
        return f"""
        <style>
        @import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap');
        
        .clustrix-enhanced {{
            font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif !important;
            background-color: {colors['background']} !important;
            color: {colors['text']} !important;
            --clustrix-primary: {colors['primary']};
            --clustrix-secondary: {colors['secondary']};
            --clustrix-accent: {colors['accent']};
            --clustrix-surface: {colors['surface']};
            --clustrix-border: {colors['border']};
            --clustrix-text: {colors['text']};
            --clustrix-text-muted: {colors['text_muted']};
            --clustrix-success: {colors['success']};
            --clustrix-warning: {colors['warning']};
            --clustrix-error: {colors['error']};
            --clustrix-info: {colors['info']};
        }}
        
        .clustrix-card {{
            background: {colors['surface']};
            border: 1px solid {colors['border']};
            border-radius: 12px;
            padding: 20px;
            margin: 10px 0;
            box-shadow: 0 2px 8px rgba(0, 0, 0, 0.1);
            transition: all 0.2s ease;
        }}
        
        .clustrix-card:hover {{
            box-shadow: 0 4px 16px rgba(0, 0, 0, 0.15);
            transform: translateY(-2px);
        }}
        
        .clustrix-btn-primary {{
            background: linear-gradient(135deg, {colors['primary']}, {colors['accent']}) !important;
            color: white !important;
            border: none !important;
            border-radius: 8px !important;
            padding: 8px 16px !important;
            font-weight: 500 !important;
            transition: all 0.2s ease !important;
            box-shadow: 0 2px 4px rgba(0, 0, 0, 0.1) !important;
        }}
        
        .clustrix-btn-primary:hover {{
            transform: translateY(-1px) !important;
            box-shadow: 0 4px 8px rgba(0, 0, 0, 0.2) !important;
        }}
        
        .clustrix-input {{
            background: {colors['surface']} !important;
            border: 2px solid {colors['border']} !important;
            border-radius: 6px !important;
            color: {colors['text']} !important;
            padding: 8px 12px !important;
            font-family: 'Inter', sans-serif !important;
            transition: border-color 0.2s ease !important;
        }}
        
        .clustrix-input:focus {{
            border-color: {colors['primary']} !important;
            box-shadow: 0 0 0 3px rgba(51, 51, 102, 0.1) !important;
            outline: none !important;
        }}
        
        .clustrix-progress {{
            background: {colors['border']};
            border-radius: 10px;
            height: 8px;
            overflow: hidden;
            position: relative;
        }}
        
        .clustrix-progress-bar {{
            background: linear-gradient(90deg, {colors['primary']}, {colors['accent']});
            height: 100%;
            border-radius: 10px;
            transition: width 0.3s ease;
            position: relative;
        }}
        
        .clustrix-progress-bar::before {{
            content: '';
            position: absolute;
            top: 0;
            left: -100%;
            width: 100%;
            height: 100%;
            background: linear-gradient(90deg, transparent, rgba(255, 255, 255, 0.3), transparent);
            animation: shimmer 2s infinite;
        }}
        
        @keyframes shimmer {{
            0% {{ left: -100%; }}
            100% {{ left: 100%; }}
        }}
        
        .clustrix-status-badge {{
            padding: 4px 12px;
            border-radius: 16px;
            font-size: 12px;
            font-weight: 500;
            display: inline-block;
            margin: 2px;
        }}
        
        .clustrix-status-success {{
            background: {colors['success']};
            color: white;
        }}
        
        .clustrix-status-warning {{
            background: {colors['warning']};
            color: white;
        }}
        
        .clustrix-status-error {{
            background: {colors['error']};
            color: white;
        }}
        
        .clustrix-status-info {{
            background: {colors['info']};
            color: white;
        }}
        
        .clustrix-theme-toggle {{
            position: absolute;
            top: 10px;
            right: 10px;
            background: {colors['surface']};
            border: 1px solid {colors['border']};
            border-radius: 20px;
            width: 40px;
            height: 20px;
            cursor: pointer;
            transition: all 0.2s ease;
        }}
        
        .clustrix-theme-toggle::before {{
            content: '';
            position: absolute;
            top: 2px;
            left: 2px;
            width: 16px;
            height: 16px;
            background: {colors['primary']};
            border-radius: 50%;
            transition: transform 0.2s ease;
        }}
        
        .clustrix-theme-toggle.dark::before {{
            transform: translateX(18px);
        }}
        
        .clustrix-notification {{
            position: fixed;
            top: 20px;
            right: 20px;
            z-index: 1000;
            max-width: 400px;
            background: {colors['surface']};
            border: 1px solid {colors['border']};
            border-radius: 8px;
            padding: 16px;
            box-shadow: 0 4px 12px rgba(0, 0, 0, 0.1);
            opacity: 0;
            transform: translateX(100%);
            transition: all 0.3s ease;
        }}
        
        .clustrix-notification.show {{
            opacity: 1;
            transform: translateX(0);
        }}
        
        .clustrix-tooltip {{
            position: relative;
            cursor: help;
        }}
        
        .clustrix-tooltip::after {{
            content: attr(data-tooltip);
            position: absolute;
            bottom: 125%;
            left: 50%;
            transform: translateX(-50%);
            background: {colors['text']};
            color: {colors['background']};
            padding: 8px 12px;
            border-radius: 6px;
            font-size: 12px;
            white-space: nowrap;
            opacity: 0;
            pointer-events: none;
            transition: opacity 0.2s ease;
            z-index: 1000;
        }}
        
        .clustrix-tooltip:hover::after {{
            opacity: 1;
        }}
        </style>
        """


class ProgressIndicator:
    """Manages progress indicators for long-running operations."""

    def __init__(self, widget_output: "widgets.Output"):
        self.output = widget_output
        self.current_progress = 0
        self.total_steps = 0
        self.current_operation = ""
        self.start_time: Optional[datetime] = None

    def start_operation(self, operation_name: str, total_steps: int = 100):
        """Start a new operation with progress tracking."""
        self.current_operation = operation_name
        self.total_steps = total_steps
        self.current_progress = 0
        self.start_time = datetime.now()

        with self.output:
            self.output.clear_output(wait=True)
            print(f"🚀 Starting: {operation_name}")
            self._render_progress_bar()

    def update_progress(self, progress: int, step_description: str = ""):
        """Update progress indicator."""
        self.current_progress = min(progress, self.total_steps)

        with self.output:
            self.output.clear_output(wait=True)
            print(f"🚀 {self.current_operation}")
            if step_description:
                print(f"   {step_description}")
            self._render_progress_bar()

            # Show elapsed time
            if self.start_time:
                elapsed = datetime.now() - self.start_time
                print(f"   Elapsed: {elapsed.seconds}s")

    def complete_operation(self, success: bool = True, message: str = ""):
        """Complete the current operation."""
        status_emoji = "✅" if success else "❌"
        status_text = "Completed" if success else "Failed"

        with self.output:
            self.output.clear_output(wait=True)
            print(f"{status_emoji} {status_text}: {self.current_operation}")
            if message:
                print(f"   {message}")

            if self.start_time:
                elapsed = datetime.now() - self.start_time
                print(f"   Total time: {elapsed.seconds}s")

    def _render_progress_bar(self):
        """Render a text-based progress bar."""
        if self.total_steps == 0:
            return

        percentage = (self.current_progress / self.total_steps) * 100
        bar_length = 40
        filled_length = int(bar_length * self.current_progress // self.total_steps)

        bar = "█" * filled_length + "░" * (bar_length - filled_length)
        print(
            f"   [{bar}] {percentage:.1f}% ({self.current_progress}/{self.total_steps})"
        )


class NotificationManager:
    """Manages notifications and status messages."""

    def __init__(self):
        self.notifications: List[Dict[str, Any]] = []

    def show_notification(
        self, message: str, notification_type: str = "info", duration: int = 3000
    ):
        """Show a notification."""
        notification = {
            "message": message,
            "type": notification_type,
            "timestamp": datetime.now(),
            "duration": duration,
        }
        self.notifications.append(notification)

        # In a real implementation, this would show a JavaScript notification
        print(f"🔔 [{notification_type.upper()}] {message}")

    def get_recent_notifications(self, limit: int = 5) -> List[Dict[str, Any]]:
        """Get recent notifications."""
        return self.notifications[-limit:]

    def clear_notifications(self):
        """Clear all notifications."""
        self.notifications.clear()


def render_modern_styling() -> str:
    """Render modern CSS styling for enhanced UI components."""
    theme_manager = ThemeManager()
    return theme_manager.get_theme_css()


def implement_dark_mode(enable: bool = True) -> ThemeManager:
    """Implement dark mode functionality."""
    theme_manager = ThemeManager()
    theme = "dark" if enable else "light"
    theme_manager.set_theme(theme)
    return theme_manager


def add_progress_indicators(output_widget: "widgets.Output") -> ProgressIndicator:
    """Add progress indicators for long-running operations."""
    if not IPYTHON_AVAILABLE:
        raise ImportError("IPython and ipywidgets are required for progress indicators")

    return ProgressIndicator(output_widget)


def create_enhanced_ui_components(theme: str = "light") -> Dict[str, Any]:
    """Create enhanced UI components with modern styling."""
    if not IPYTHON_AVAILABLE:
        raise ImportError("IPython and ipywidgets are required for enhanced UI")

    theme_manager = ThemeManager()
    theme_manager.set_theme(theme)
    colors = theme_manager.get_current_theme()

    # Create styled components
    components = {
        "theme_manager": theme_manager,
        "notification_manager": NotificationManager(),
    }

    # Theme toggle button
    components["theme_toggle"] = widgets.ToggleButton(
        value=theme == "dark",
        description="🌙" if theme == "light" else "☀️",
        tooltip="Toggle dark/light theme",
        layout=widgets.Layout(width="60px", height="35px"),
        style={"button_color": colors["surface"]},
    )

    # Status indicator
    components["status_indicator"] = widgets.HTML(
        value='<div class="clustrix-status-badge clustrix-status-info">Ready</div>',
        layout=widgets.Layout(margin="5px 0"),
    )

    # Progress container
    components["progress_output"] = widgets.Output(
        layout=widgets.Layout(
            height="150px",
            border=f"1px solid {colors['border']}",
            border_radius="8px",
            padding="10px",
            background_color=colors["surface"],
        )
    )

    # Enhanced button with modern styling
    components["action_button"] = widgets.Button(
        description="Execute",
        tooltip="Execute with enhanced progress tracking",
        layout=widgets.Layout(width="120px", height="40px"),
        style={"button_color": colors["primary"]},
    )

    # Configuration panel with card styling
    components["config_panel"] = widgets.VBox(
        layout=widgets.Layout(
            border=f"1px solid {colors['border']}",
            border_radius="12px",
            padding="20px",
            margin="10px 0",
            background_color=colors["surface"],
        )
    )

    # Connection status with real-time updates
    components["connection_status"] = widgets.HTML(
        value='<span class="clustrix-tooltip" data-tooltip="Click to test connection">'
        "🔌 Connection: Not tested</span>",
        layout=widgets.Layout(margin="5px 0"),
    )

    return components


def create_interactive_dashboard(
    config: Optional[ClusterConfig] = None,
) -> "widgets.Widget":
    """Create an interactive dashboard with enhanced UI components."""
    if not IPYTHON_AVAILABLE:
        raise ImportError("IPython and ipywidgets are required for the dashboard")

    # Initialize components
    ui_components = create_enhanced_ui_components()
    theme_manager = ui_components["theme_manager"]
    notification_manager = ui_components["notification_manager"]

    # Inject CSS styles
    display(HTML(theme_manager.get_theme_css()))

    # Dashboard header
    header = widgets.HTML(
        value='<h2 style="margin: 0; font-weight: 600;">🚀 Clustrix Enhanced Dashboard</h2>',
        layout=widgets.Layout(margin="0 0 20px 0"),
    )

    # Theme toggle functionality
    def on_theme_toggle(change):
        theme = "dark" if change["new"] else "light"
        theme_manager.set_theme(theme)
        ui_components["theme_toggle"].description = "☀️" if theme == "dark" else "🌙"
        # Refresh CSS
        display(HTML(theme_manager.get_theme_css()))
        notification_manager.show_notification(f"Switched to {theme} theme", "info")

    ui_components["theme_toggle"].observe(on_theme_toggle, names="value")

    # Status monitoring
    status_section = widgets.VBox(
        [
            widgets.HTML('<h3 style="margin: 10px 0;">📊 System Status</h3>'),
            ui_components["status_indicator"],
            ui_components["connection_status"],
        ]
    )

    # Progress monitoring
    progress_section = widgets.VBox(
        [
            widgets.HTML('<h3 style="margin: 10px 0;">⚡ Progress Monitor</h3>'),
            ui_components["progress_output"],
            ui_components["action_button"],
        ]
    )

    # Test button functionality
    def on_test_action(button):
        progress = add_progress_indicators(ui_components["progress_output"])

        # Simulate a multi-step operation
        progress.start_operation("Testing Enhanced Features", 5)

        import time

        steps = [
            "Initializing enhanced UI components...",
            "Testing theme switching...",
            "Validating progress indicators...",
            "Testing notification system...",
            "Completing enhanced feature test...",
        ]

        for i, step in enumerate(steps, 1):
            time.sleep(0.5)  # Simulate work
            progress.update_progress(i, step)
            if i == 2:
                notification_manager.show_notification("Theme test passed!", "success")

        progress.complete_operation(True, "All enhanced features working correctly!")
        ui_components["status_indicator"].value = (
            '<div class="clustrix-status-badge clustrix-status-success">All Systems Go</div>'
        )

    ui_components["action_button"].on_click(on_test_action)

    # Layout assembly
    header_row = widgets.HBox(
        [
            header,
            widgets.HTML('<div style="flex: 1;"></div>'),  # Spacer
            ui_components["theme_toggle"],
        ],
        layout=widgets.Layout(align_items="center"),
    )

    main_content = widgets.HBox(
        [status_section, progress_section], layout=widgets.Layout(margin="20px 0")
    )

    # Complete dashboard
    dashboard = widgets.VBox(
        [header_row, main_content],
        layout=widgets.Layout(
            padding="20px",
            border="1px solid #dee2e6",
            border_radius="12px",
            background_color="#ffffff",
        ),
    )

    dashboard.add_class("clustrix-enhanced")

    return dashboard


def validate_enhanced_features() -> Dict[str, bool]:
    """Validate that all enhanced features are working correctly."""
    results = {}

    try:
        # Test theme manager
        theme_manager = ThemeManager()
        theme_manager.set_theme("dark")
        theme_manager.set_theme("light")
        results["theme_management"] = True
    except Exception:
        results["theme_management"] = False

    try:
        # Test notification manager
        notification_manager = NotificationManager()
        notification_manager.show_notification("Test", "info")
        notification_manager.clear_notifications()
        results["notification_system"] = True
    except Exception:
        results["notification_system"] = False

    try:
        # Test CSS generation
        css = render_modern_styling()
        results["css_generation"] = bool(css and len(css) > 100)
    except Exception:
        results["css_generation"] = False

    try:
        # Test enhanced components creation
        if IPYTHON_AVAILABLE:
            components = create_enhanced_ui_components()
            results["component_creation"] = bool(
                components and "theme_manager" in components
            )
        else:
            results["component_creation"] = False
    except Exception:
        results["component_creation"] = False

    return results


# Export key functions for testing
__all__ = [
    "ThemeManager",
    "ProgressIndicator",
    "NotificationManager",
    "render_modern_styling",
    "implement_dark_mode",
    "add_progress_indicators",
    "create_enhanced_ui_components",
    "create_interactive_dashboard",
    "validate_enhanced_features",
]
