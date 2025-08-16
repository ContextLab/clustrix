"""Enhanced notebook widget with advanced authentication options."""

import os
from typing import Optional

try:
    import ipywidgets as widgets
    from IPython.display import display

    IPYTHON_AVAILABLE = True
except ImportError:
    IPYTHON_AVAILABLE = False

from .config import ClusterConfig
from .auth_manager import AuthenticationManager
from .validation import (
    validate_cluster_auth,
    validate_ssh_key_auth,
    validate_1password_integration,
)


def create_enhanced_cluster_widget(
    config: Optional[ClusterConfig] = None,
) -> widgets.VBox:
    """
    Create enhanced cluster configuration widget with advanced authentication options.

    This widget includes:
    - Dynamic checkboxes for authentication methods
    - Conditional field visibility based on checkbox state
    - Integration with AuthenticationManager
    - Real-time validation feedback
    - 1Password storage offering
    """
    if not IPYTHON_AVAILABLE:
        raise ImportError(
            "IPython and ipywidgets are required for the widget interface"
        )

    # Initialize config if not provided
    if config is None:
        config = ClusterConfig()

    # Styling
    style = {"description_width": "150px"}
    full_layout = widgets.Layout(width="100%")
    half_layout = widgets.Layout(width="48%")

    # =============================================================================
    # Basic Cluster Configuration Section
    # =============================================================================

    basic_header = widgets.HTML(
        value='<h3 style="color: #333; margin-bottom: 10px;">🖥️ Cluster Configuration</h3>'
    )

    cluster_type = widgets.Dropdown(
        options=[
            "local",
            "ssh",
            "slurm",
            "pbs",
            "sge",
            "kubernetes",
            "aws",
            "azure",
            "gcp",
        ],
        value=config.cluster_type,
        description="Cluster Type:",
        style=style,
        layout=full_layout,
    )

    hostname = widgets.Text(
        value=config.cluster_host or "",
        placeholder="e.g., tensor01.dartmouth.edu",
        description="Hostname:",
        style=style,
        layout=full_layout,
    )

    username = widgets.Text(
        value=config.username or os.getenv("USER", ""),
        placeholder="username",
        description="Username:",
        style=style,
        layout=half_layout,
    )

    port = widgets.IntText(
        value=config.ssh_port,
        description="SSH Port:",
        style=style,
        layout=widgets.Layout(width="200px"),
    )

    # =============================================================================
    # Enhanced Authentication Options Section
    # =============================================================================

    auth_header = widgets.HTML(
        value='<h3 style="color: #333; margin-bottom: 10px;">🔐 Authentication Options</h3>'
    )

    # Password field for immediate use
    password_input = widgets.Password(
        value="",
        placeholder="Password for SSH setup",
        description="Password:",
        style=style,
        layout=full_layout,
    )

    password_help = widgets.HTML(
        value='<small style="color: #666;">Used for SSH key setup and authentication fallback</small>'
    )

    # 1Password Option with conditional field
    use_1password = widgets.Checkbox(
        value=config.use_1password,
        description="Use 1Password",
        style={"description_width": "initial"},
        tooltip="Enable 1Password CLI integration for secure credential storage",
    )

    onepassword_note = widgets.Text(
        value=config.onepassword_note,
        placeholder="e.g., clustrix-tensor01 (optional)",
        description="Note name:",
        style=style,
        layout=widgets.Layout(width="100%", display="none"),  # Hidden by default
    )

    onepassword_help = widgets.HTML(
        value='<small style="color: #666;">Leave blank to use default naming: clustrix-{hostname}</small>',
        layout=widgets.Layout(display="none"),
    )

    # Environment Variable Option with conditional field
    use_env_password = widgets.Checkbox(
        value=config.use_env_password,
        description="Use Environment Variable",
        style={"description_width": "initial"},
        tooltip="Use an environment variable for password storage",
    )

    password_env_var = widgets.Text(
        value=config.password_env_var,
        placeholder="e.g., CLUSTER_PASSWORD",
        description="Variable name:",
        style=style,
        layout=widgets.Layout(width="100%", display="none"),  # Hidden by default
    )

    env_var_help = widgets.HTML(
        value='<small style="color: #666;">Set with: export VARIABLE_NAME="your_password"</small>',
        layout=widgets.Layout(display="none"),
    )

    # Authentication status display
    auth_status = widgets.HTML(
        value='<div style="padding: 10px; border-radius: 4px; background: #f8f9fa; border: 1px solid #dee2e6;">'
        '<i style="color: #6c757d;">Authentication methods will be configured based on your selections</i></div>'
    )

    # =============================================================================
    # SSH Key Setup Section
    # =============================================================================

    ssh_header = widgets.HTML(
        value='<h3 style="color: #333; margin-bottom: 10px;">🔑 SSH Key Setup</h3>'
    )

    setup_button = widgets.Button(
        description="Setup SSH Keys",
        button_style="primary",
        icon="key",
        tooltip="Generate and deploy SSH keys using enhanced authentication",
    )

    force_refresh = widgets.Checkbox(
        value=False,
        description="Force key refresh",
        tooltip="Replace existing SSH keys",
    )

    # Status and output area
    status_output = widgets.Output(
        layout=widgets.Layout(
            height="200px",
            width="100%",
            overflow_y="auto",
            border="1px solid #ddd",
            border_radius="4px",
            padding="10px",
            margin="10px 0px",
            background_color="#f8f9fa",
        )
    )

    # =============================================================================
    # Dynamic Field Visibility Handlers
    # =============================================================================

    def on_1password_toggle(change):
        """Show/hide 1Password note field and update status"""
        if change["new"]:
            onepassword_note.layout.display = "flex"
            onepassword_help.layout.display = "block"

            # Check 1Password availability
            if validate_1password_integration():
                auth_status.value = (
                    '<div style="padding: 10px; border-radius: 4px; background: #d4edda; border: 1px solid #c3e6cb;">'
                    "✅ 1Password CLI available and authenticated</div>"
                )
            else:
                auth_status.value = (
                    '<div style="padding: 10px; border-radius: 4px; background: #f8d7da; border: 1px solid #f5c6cb;">'
                    "⚠️ 1Password CLI not found - install with: <code>brew install 1password-cli</code></div>"
                )
                use_1password.value = False
                onepassword_note.layout.display = "none"
                onepassword_help.layout.display = "none"
        else:
            onepassword_note.layout.display = "none"
            onepassword_help.layout.display = "none"
            update_auth_status()

    def on_env_toggle(change):
        """Show/hide environment variable field"""
        if change["new"]:
            password_env_var.layout.display = "flex"
            env_var_help.layout.display = "block"
        else:
            password_env_var.layout.display = "none"
            env_var_help.layout.display = "none"
        update_auth_status()

    def update_auth_status():
        """Update authentication status based on current selections"""
        methods = []
        if use_1password.value:
            methods.append("1Password")
        if use_env_password.value:
            methods.append("Environment Variable")

        if methods:
            method_list = ", ".join(methods)
            auth_status.value = (
                f'<div style="padding: 10px; border-radius: 4px; background: #d1ecf1; border: 1px solid #bee5eb;">'
                f"🔐 Authentication methods: {method_list}</div>"
            )
        else:
            auth_status.value = (
                '<div style="padding: 10px; border-radius: 4px; background: #f8f9fa; border: 1px solid #dee2e6;">'
                '<i style="color: #6c757d;">Using standard SSH key authentication</i></div>'
            )

    # Attach observers
    use_1password.observe(on_1password_toggle, names="value")
    use_env_password.observe(on_env_toggle, names="value")

    # Initialize field visibility WITHOUT triggering validation
    if use_1password.value:
        # Just show the fields, don't validate (which triggers 1Password popup)
        onepassword_note.layout.display = "flex"
        onepassword_help.layout.display = "block"
    if use_env_password.value:
        password_env_var.layout.display = "flex"
        password_help.layout.display = "block"

    # =============================================================================
    # Enhanced SSH Setup Handler
    # =============================================================================

    def on_setup_ssh_keys(b):
        """Enhanced SSH setup with authentication fallback chain"""
        with status_output:
            status_output.clear_output()

            # Create configuration from widget values
            widget_config = ClusterConfig(
                cluster_type=cluster_type.value,
                cluster_host=hostname.value,
                username=username.value,
                ssh_port=port.value,
                use_1password=use_1password.value,
                onepassword_note=onepassword_note.value,
                use_env_password=use_env_password.value,
                password_env_var=password_env_var.value,
            )

            print(f"🔐 Setting up SSH keys for {username.value}@{hostname.value}")
            print(f"    Port: {port.value}")
            print(f"    Cluster type: {cluster_type.value}")

            # Show configured authentication methods
            if widget_config.use_1password or widget_config.use_env_password:
                print("\\n🔧 Authentication methods configured:")
                if widget_config.use_1password:
                    note_name = (
                        widget_config.onepassword_note
                        or f"clustrix-{widget_config.cluster_host}"
                    )
                    print(f"    • 1Password (note: {note_name})")
                if widget_config.use_env_password:
                    print(
                        f"    • Environment variable: ${widget_config.password_env_var}"
                    )

            # Initialize authentication manager
            auth_manager = AuthenticationManager(widget_config)

            # Set widget password if provided
            if password_input.value:
                auth_manager.set_widget_password(password_input.value)
                print("    • Widget password field")

            print()

            # Validate current configuration
            print("🔍 Validating authentication configuration...")
            validation_results = auth_manager.validate_configuration()

            has_working_auth = any(validation_results.values())
            if not has_working_auth and not password_input.value:
                print("⚠️  No working authentication methods found")
                print("   Please either:")
                print("   • Enter a password in the password field, or")
                print("   • Configure 1Password with valid credentials, or")
                print("   • Set the specified environment variable")
                return

            print()

            # Get password through authentication chain
            print("🔐 Obtaining password for SSH key setup...")
            password = auth_manager.get_password_for_setup()

            if not password:
                print("❌ Could not obtain password for SSH setup")
                print(
                    "   Authentication chain exhausted - please check your configuration"
                )
                return

            print()

            # Test authentication on real cluster
            print("🧪 Validating authentication on cluster...")
            if validate_cluster_auth(widget_config, password):
                print("✅ Authentication validated successfully!")
            else:
                print("⚠️  Authentication validation failed")
                print("   Continuing with SSH key setup anyway...")

            print()

            # Import and run SSH setup
            try:
                from .ssh_utils import setup_ssh_keys

                print("🔧 Setting up SSH keys...")
                result = setup_ssh_keys(
                    hostname=widget_config.cluster_host,
                    username=widget_config.username,
                    password=password,
                    port=widget_config.ssh_port,
                    key_type="ed25519",  # Use secure key type
                    force_refresh=force_refresh.value,
                )

                if result:
                    print("✅ SSH key setup completed successfully!")

                    # Test SSH key authentication
                    print("\\n🧪 Testing SSH key authentication...")
                    if validate_ssh_key_auth(widget_config):
                        print("✅ SSH key authentication working!")

                        # Update auth status
                        auth_status.value = (
                            '<div style="padding: 10px; border-radius: 4px; '
                            'background: #d4edda; border: 1px solid #c3e6cb;">'
                            "✅ SSH keys configured and working</div>"
                        )
                    else:
                        print("⚠️  SSH key authentication not working yet")
                        print(
                            "   Keys may need time to propagate or cluster may have additional requirements"
                        )
                else:
                    print("❌ SSH key setup failed")

            except Exception as e:
                print(f"❌ SSH setup error: {e}")

            # Clear password field for security
            password_input.value = ""

    setup_button.on_click(on_setup_ssh_keys)

    # =============================================================================
    # Validation Button
    # =============================================================================

    validate_button = widgets.Button(
        description="Validate Configuration",
        button_style="info",
        icon="check-circle",
        tooltip="Test all configured authentication methods",
    )

    def on_validate_config(b):
        """Validate the current configuration"""
        with status_output:
            status_output.clear_output()

            widget_config = ClusterConfig(
                cluster_type=cluster_type.value,
                cluster_host=hostname.value,
                username=username.value,
                ssh_port=port.value,
                use_1password=use_1password.value,
                onepassword_note=onepassword_note.value,
                use_env_password=use_env_password.value,
                password_env_var=password_env_var.value,
            )

            print("🔍 Validating cluster configuration...")
            print(f"Target: {username.value}@{hostname.value}:{port.value}")
            print()

            # Run validation
            from .validation import run_comprehensive_validation

            results = run_comprehensive_validation(widget_config)

            # Update status based on results
            working_methods = [k for k, v in results.items() if v is True]
            if working_methods:
                method_names = [k.replace("_", " ").title() for k in working_methods]
                auth_status.value = (
                    f'<div style="padding: 10px; border-radius: 4px; background: #d4edda; border: 1px solid #c3e6cb;">'
                    f'✅ Working methods: {", ".join(method_names)}</div>'
                )
            else:
                auth_status.value = (
                    '<div style="padding: 10px; border-radius: 4px; background: #f8d7da; border: 1px solid #f5c6cb;">'
                    "❌ No working authentication methods found</div>"
                )

    validate_button.on_click(on_validate_config)

    # =============================================================================
    # Widget Layout Assembly
    # =============================================================================

    # Basic configuration section
    basic_section = widgets.VBox(
        [basic_header, cluster_type, hostname, widgets.HBox([username, port])]
    )

    # Authentication configuration section
    auth_section = widgets.VBox(
        [
            auth_header,
            password_input,
            password_help,
            widgets.HTML("<br>"),
            use_1password,
            onepassword_note,
            onepassword_help,
            widgets.HTML("<br>"),
            use_env_password,
            password_env_var,
            env_var_help,
            widgets.HTML("<br>"),
            auth_status,
        ]
    )

    # SSH setup section
    ssh_section = widgets.VBox(
        [
            ssh_header,
            widgets.HBox([setup_button, validate_button, force_refresh]),
            status_output,
        ]
    )

    # Complete widget
    complete_widget = widgets.VBox(
        [
            basic_section,
            widgets.HTML('<hr style="margin: 20px 0;">'),
            auth_section,
            widgets.HTML('<hr style="margin: 20px 0;">'),
            ssh_section,
        ]
    )

    return complete_widget


def display_enhanced_widget():
    """Display the enhanced cluster configuration widget"""
    if not IPYTHON_AVAILABLE:
        print("❌ Enhanced widget requires IPython and ipywidgets")
        print("Install with: pip install ipywidgets")
        return

    widget = create_enhanced_cluster_widget()
    display(widget)
    return widget
