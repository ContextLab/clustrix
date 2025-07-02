# Clustrix Modern Widget Component Specification

## Overview
This document provides a comprehensive specification of every component in the modern Clustrix widget GUI based on the visual mockups. Each component is detailed with appearance, behavior, location, function, and visibility context.

## Visual Analysis Reference
- **Local Basic**: `clustrix_mockup_local_basic.png`
- **Local Advanced**: `clustrix_mockup_local_advanced.png`  
- **Remote Basic**: `clustrix_mockup_remote_basic.png`

## Component Specifications

### Row 1: Profile Management

#### 1.1 "Active profile:" Label
- **Appearance**: Black text, standard font, left-aligned
- **Behavior**: Static text label
- **Location**: Far left of top row
- **Function**: Label for profile dropdown
- **Context**: Always visible

#### 1.2 Profile Dropdown
- **Appearance**: 
  - White background with black border
  - Dropdown arrow on right
  - Width: ~250px
  - Text: "Local single-core" / "SLURM University Cluster"
- **Behavior**: Dropdown menu with profile selection
- **Location**: Top row, after "Active profile:" label
- **Function**: Select active configuration profile
- **Context**: Always visible

#### 1.3 Add Profile Button (+)
- **Appearance**: 
  - Dark purple/navy background (#3e4a61)
  - White "+" symbol
  - Square shape, ~35px x 35px
- **Behavior**: Clickable button
- **Location**: Top row, immediately right of profile dropdown
- **Function**: Clone current profile to create new one
- **Context**: Always visible

#### 1.4 Remove Profile Button (−)
- **Appearance**: 
  - Dark purple/navy background (#3e4a61)
  - White "−" symbol
  - Square shape, ~35px x 35px
- **Behavior**: Clickable button
- **Location**: Top row, immediately right of add profile button
- **Function**: Delete currently selected profile
- **Context**: Always visible

### Row 2: Configuration Management

#### 2.1 "Config filename:" Label
- **Appearance**: Black text, standard font, left-aligned
- **Behavior**: Static text label
- **Location**: Second row, far left
- **Function**: Label for config filename field
- **Context**: Always visible

#### 2.2 Config Filename Field
- **Appearance**: 
  - White background with black border
  - Width: ~180px
  - Text: "clustrix.yml"
- **Behavior**: Editable text input
- **Location**: Second row, after "Config filename:" label
- **Function**: Specify configuration file name for save/load
- **Context**: Always visible

#### 2.3 Save Config Button (📄)
- **Appearance**: 
  - Light gray background
  - Dark document/save icon
  - Square shape, ~35px x 35px
- **Behavior**: Clickable button
- **Location**: Second row, immediately right of config filename field
- **Function**: Save current configuration to file
- **Context**: Always visible

#### 2.4 Load Config Button (📁)
- **Appearance**: 
  - Orange/amber background
  - Dark folder/load icon
  - Square shape, ~35px x 35px
- **Behavior**: Clickable button
- **Location**: Second row, immediately right of save button
- **Function**: Load configuration from file
- **Context**: Always visible

#### 2.5 Apply Button
- **Appearance**: 
  - Dark purple/navy background (#3e4a61)
  - White "Apply" text
  - Rectangular shape, ~80px x 35px
- **Behavior**: Clickable button
- **Location**: Second row, right side
- **Function**: Apply current widget settings to profile
- **Context**: Always visible

#### 2.6 Test Connect Button
- **Appearance**: 
  - Dark purple/navy background (#3e4a61)
  - White "Test connect" text
  - Rectangular shape, ~110px x 35px
- **Behavior**: Clickable button
- **Location**: Second row, right of Apply button
- **Function**: Test SSH connection to cluster
- **Context**: Always visible

#### 2.7 Test Submit Button
- **Appearance**: 
  - Dark purple/navy background (#3e4a61)
  - White "Test submit" text
  - Rectangular shape, ~110px x 35px
- **Behavior**: Clickable button
- **Location**: Second row, far right
- **Function**: Test job submission to cluster
- **Context**: Always visible

### Row 3: Core Cluster Configuration

#### 3.1 "Cluster type:" Label
- **Appearance**: Black text, standard font, left-aligned
- **Behavior**: Static text label
- **Location**: Third row, far left
- **Function**: Label for cluster type dropdown
- **Context**: Always visible

#### 3.2 Cluster Type Dropdown
- **Appearance**: 
  - White background with black border
  - Dropdown arrow on right
  - Width: ~120px
  - Text: "local" / "slurm"
- **Behavior**: Dropdown menu with cluster type options
- **Location**: Third row, after "Cluster type:" label
- **Function**: Select cluster scheduler type
- **Context**: Always visible

#### 3.3 "CPUs:" Label
- **Appearance**: Black text, standard font, left-aligned
- **Behavior**: Static text label
- **Location**: Third row, after cluster type dropdown
- **Function**: Label for CPU count field
- **Context**: Always visible

#### 3.4 CPU Count Field
- **Appearance**: 
  - White background with black border
  - Width: ~50px
  - Text: "1" / "8"
  - Small up/down arrows on right
- **Behavior**: Numeric input with spinner controls
- **Location**: Third row, after "CPUs:" label
- **Function**: Set number of CPU cores to request
- **Context**: Always visible

#### 3.5 "RAM:" Label
- **Appearance**: Black text, standard font, left-aligned
- **Behavior**: Static text label
- **Location**: Third row, after CPU field
- **Function**: Label for RAM amount field
- **Context**: Always visible

#### 3.6 RAM Amount Field
- **Appearance**: 
  - White background with black border
  - Width: ~80px
  - Text: "16.25"
- **Behavior**: Numeric input (float)
- **Location**: Third row, after "RAM:" label
- **Function**: Set amount of RAM to request
- **Context**: Always visible

#### 3.7 "GB" Label
- **Appearance**: Gray text, standard font
- **Behavior**: Static text label
- **Location**: Third row, immediately after RAM field
- **Function**: Unit indicator for RAM amount
- **Context**: Always visible

#### 3.8 "Time:" Label
- **Appearance**: Black text, standard font, left-aligned
- **Behavior**: Static text label
- **Location**: Third row, after RAM section
- **Function**: Label for time limit field
- **Context**: Always visible

#### 3.9 Time Limit Field
- **Appearance**: 
  - White background with black border
  - Width: ~80px
  - Text: "01:00:00" / "05:00:00"
- **Behavior**: Time format input (HH:MM:SS)
- **Location**: Third row, after "Time:" label
- **Function**: Set job time limit
- **Context**: Always visible

### Row 4: Remote Cluster Configuration (Context: Only when cluster type ≠ "local")

#### 4.1 "Host/address:" Label
- **Appearance**: Black text, standard font, left-aligned
- **Behavior**: Static text label
- **Location**: Fourth row, far left
- **Function**: Label for hostname field
- **Context**: Visible only when cluster type is remote (slurm, pbs, sge, ssh, kubernetes)

#### 4.2 Hostname Field
- **Appearance**: 
  - White background with black border
  - Width: ~200px
  - Text: "slurm.university.edu"
- **Behavior**: Editable text input
- **Location**: Fourth row, after "Host/address:" label
- **Function**: Enter cluster hostname/IP address
- **Context**: Visible only when cluster type is remote

#### 4.3 "Port:" Label
- **Appearance**: Black text, standard font, left-aligned
- **Behavior**: Static text label
- **Location**: Fourth row, after hostname field
- **Function**: Label for port number field
- **Context**: Visible only when cluster type is remote

#### 4.4 Port Number Field
- **Appearance**: 
  - White background with black border
  - Width: ~60px
  - Text: "22"
  - Small up/down arrows on right
- **Behavior**: Numeric input with spinner controls
- **Location**: Fourth row, after "Port:" label
- **Function**: Set SSH port number
- **Context**: Visible only when cluster type is remote

#### 4.5 "Username:" Label
- **Appearance**: Black text, standard font, left-aligned
- **Behavior**: Static text label
- **Location**: Fourth row, after port field
- **Function**: Label for username field
- **Context**: Visible only when cluster type is remote

#### 4.6 Username Field
- **Appearance**: 
  - White background with black border
  - Width: ~120px
  - Text: "researcher"
- **Behavior**: Editable text input
- **Location**: Fourth row, after "Username:" label
- **Function**: Enter SSH username
- **Context**: Visible only when cluster type is remote

### Row 5: SSH Authentication (Context: Only when cluster type ≠ "local")

#### 5.1 "SSH key file:" Label
- **Appearance**: Black text, standard font, left-aligned
- **Behavior**: Static text label
- **Location**: Fifth row, far left
- **Function**: Label for SSH key file path
- **Context**: Visible only when cluster type is remote

#### 5.2 SSH Key File Field
- **Appearance**: 
  - White background with black border
  - Width: ~180px
  - Text: "~/.ssh/id_rsa"
- **Behavior**: Editable text input
- **Location**: Fifth row, after "SSH key file:" label
- **Function**: Specify path to SSH private key
- **Context**: Visible only when cluster type is remote

#### 5.3 "Refresh:" Label
- **Appearance**: Black text, standard font, left-aligned
- **Behavior**: Static text label
- **Location**: Fifth row, after SSH key field
- **Function**: Label for refresh button
- **Context**: Visible only when cluster type is remote

#### 5.4 Refresh Keys Button
- **Appearance**: 
  - Light gray background with black border
  - Width: ~60px, height: ~25px
  - No visible text/icon in mockup
- **Behavior**: Clickable button
- **Location**: Fifth row, after "Refresh:" label
- **Function**: Refresh available SSH key list
- **Context**: Visible only when cluster type is remote

#### 5.5 "Password:" Label
- **Appearance**: Black text, standard font, left-aligned
- **Behavior**: Static text label
- **Location**: Fifth row, after refresh button
- **Function**: Label for password field
- **Context**: Visible only when cluster type is remote

#### 5.6 Password Field
- **Appearance**: 
  - White background with black border
  - Width: ~120px
  - Text: "••••••••" (masked)
- **Behavior**: Password input (masked text)
- **Location**: Fifth row, after "Password:" label
- **Function**: Enter SSH password
- **Context**: Visible only when cluster type is remote

### Row 6: Additional Authentication Options (Context: Only when cluster type ≠ "local")

#### 6.1 "Local env var:" Label
- **Appearance**: Black text, standard font, left-aligned
- **Behavior**: Static text label
- **Location**: Sixth row, far left
- **Function**: Label for environment variable field
- **Context**: Visible only when cluster type is remote

#### 6.2 Environment Variable Field
- **Appearance**: 
  - White background with black border
  - Width: ~150px
  - Text: "MY_PASSWORD"
- **Behavior**: Editable text input
- **Location**: Sixth row, after "Local env var:" label
- **Function**: Name of environment variable containing password
- **Context**: Visible only when cluster type is remote

#### 6.3 "1password:" Label
- **Appearance**: Black text, standard font, left-aligned
- **Behavior**: Static text label
- **Location**: Sixth row, after env var field
- **Function**: Label for 1Password checkbox
- **Context**: Visible only when cluster type is remote

#### 6.4 1Password Checkbox
- **Appearance**: 
  - White background with black border
  - Checkmark when enabled
  - Square shape, ~20px x 20px
- **Behavior**: Clickable checkbox
- **Location**: Sixth row, after "1password:" label
- **Function**: Enable 1Password integration
- **Context**: Visible only when cluster type is remote

#### 6.5 "Home dir:" Label
- **Appearance**: Black text, standard font, left-aligned
- **Behavior**: Static text label
- **Location**: Sixth row, after 1Password checkbox
- **Function**: Label for home directory field
- **Context**: Visible only when cluster type is remote

#### 6.6 Home Directory Field
- **Appearance**: 
  - White background with black border
  - Width: ~150px
  - Text: "/home/researc" (truncated)
- **Behavior**: Editable text input
- **Location**: Sixth row, after "Home dir:" label
- **Function**: Specify remote home directory path
- **Context**: Visible only when cluster type is remote

### Row 7: Action Buttons for Remote (Context: Only when cluster type ≠ "local")

#### 7.1 Advanced Settings Button
- **Appearance**: 
  - Dark purple/navy background (#3e4a61)
  - White "Advanced settings" text
  - Rectangular shape, ~150px x 35px
- **Behavior**: Toggle button
- **Location**: Seventh row, left side
- **Function**: Show/hide advanced configuration section
- **Context**: Always visible

#### 7.2 Auto Setup SSH Keys Button
- **Appearance**: 
  - Dark purple/navy background (#3e4a61)
  - White "Auto setup SSH keys" text
  - Rectangular shape, ~180px x 35px
- **Behavior**: Clickable button
- **Location**: Seventh row, right side
- **Function**: Automatically configure SSH key authentication
- **Context**: Visible only when cluster type is remote

### Advanced Settings Section (Context: Only when "Advanced settings" button is clicked)

#### 8.1 "Package manager:" Label
- **Appearance**: Black text, standard font, left-aligned
- **Behavior**: Static text label
- **Location**: Advanced section, first row, far left
- **Function**: Label for package manager dropdown
- **Context**: Visible only when advanced settings expanded

#### 8.2 Package Manager Dropdown
- **Appearance**: 
  - White background with black border
  - Dropdown arrow on right
  - Width: ~100px
  - Text: "auto"
- **Behavior**: Dropdown menu with package manager options
- **Location**: Advanced section, first row, after label
- **Function**: Select package manager (pip, conda, auto)
- **Context**: Visible only when advanced settings expanded

#### 8.3 "Python executable:" Label
- **Appearance**: Black text, standard font, left-aligned
- **Behavior**: Static text label
- **Location**: Advanced section, first row, after package manager
- **Function**: Label for Python executable field
- **Context**: Visible only when advanced settings expanded

#### 8.4 Python Executable Field
- **Appearance**: 
  - White background with black border
  - Width: ~120px
  - Text: "python"
- **Behavior**: Editable text input
- **Location**: Advanced section, first row, after label
- **Function**: Specify Python executable path
- **Context**: Visible only when advanced settings expanded

#### 8.5 "Clone env:" Label
- **Appearance**: Black text, standard font, left-aligned
- **Behavior**: Static text label
- **Location**: Advanced section, first row, after Python field
- **Function**: Label for clone environment checkbox
- **Context**: Visible only when advanced settings expanded

#### 8.6 Clone Environment Checkbox
- **Appearance**: 
  - White background with black border
  - Checkmark when enabled
  - Square shape, ~20px x 20px
- **Behavior**: Clickable checkbox
- **Location**: Advanced section, first row, after label
- **Function**: Enable environment cloning
- **Context**: Visible only when advanced settings expanded

#### 8.7 "Env variables:" Label
- **Appearance**: Black text, standard font, left-aligned
- **Behavior**: Static text label
- **Location**: Advanced section, second row, far left
- **Function**: Label for environment variables section
- **Context**: Visible only when advanced settings expanded

#### 8.8 Environment Variables Dropdown
- **Appearance**: 
  - White background with black border
  - Dropdown arrow on right
  - Width: ~200px
  - Text: "KEY1=value1" (placeholder/gray when empty)
- **Behavior**: Dropdown/selection list for environment variables
- **Location**: Advanced section, second row, after label
- **Function**: Select/view environment variables
- **Context**: Visible only when advanced settings expanded

#### 8.9 Add Env Variable Button (+)
- **Appearance**: 
  - Dark purple/navy background (#3e4a61)
  - White "+" symbol
  - Square shape, ~25px x 25px
- **Behavior**: Clickable button
- **Location**: Advanced section, second row, after env variables dropdown
- **Function**: Add new environment variable
- **Context**: Visible only when advanced settings expanded

#### 8.10 Remove Env Variable Button (−)
- **Appearance**: 
  - Dark purple/navy background (#3e4a61)
  - White "−" symbol
  - Square shape, ~25px x 25px
- **Behavior**: Clickable button
- **Location**: Advanced section, second row, after add button
- **Function**: Remove selected environment variable
- **Context**: Visible only when advanced settings expanded

#### 8.11 "Modules:" Label
- **Appearance**: Black text, standard font, left-aligned
- **Behavior**: Static text label
- **Location**: Advanced section, second row, after env var buttons
- **Function**: Label for modules section
- **Context**: Visible only when advanced settings expanded

#### 8.12 Modules Dropdown
- **Appearance**: 
  - White background with black border
  - Dropdown arrow on right
  - Width: ~120px
  - Text: "module1" (placeholder/gray when empty)
- **Behavior**: Dropdown/selection list for modules
- **Location**: Advanced section, second row, after label
- **Function**: Select/view loaded modules
- **Context**: Visible only when advanced settings expanded

#### 8.13 Add Module Button (+)
- **Appearance**: 
  - Dark purple/navy background (#3e4a61)
  - White "+" symbol
  - Square shape, ~25px x 25px
- **Behavior**: Clickable button
- **Location**: Advanced section, second row, after modules dropdown
- **Function**: Add new module to load
- **Context**: Visible only when advanced settings expanded

#### 8.14 Remove Module Button (−)
- **Appearance**: 
  - Dark purple/navy background (#3e4a61)
  - White "−" symbol
  - Square shape, ~25px x 25px
- **Behavior**: Clickable button
- **Location**: Advanced section, second row, after add button
- **Function**: Remove selected module
- **Context**: Visible only when advanced settings expanded

#### 8.15 "Pre-exec commands:" Label
- **Appearance**: Black text, standard font, left-aligned
- **Behavior**: Static text label
- **Location**: Advanced section, third row, far left
- **Function**: Label for pre-execution commands area
- **Context**: Visible only when advanced settings expanded

#### 8.16 Pre-execution Commands Text Area
- **Appearance**: 
  - White background with black border
  - Multi-line text area
  - Width: ~600px, Height: ~100px
  - Text: Example commands like "source /path/to/setup.sh"
- **Behavior**: Multi-line editable text input
- **Location**: Advanced section, third row, spanning most of width
- **Function**: Enter shell commands to run before job execution
- **Context**: Visible only when advanced settings expanded

## Layout Structure Summary

### Row Organization:
1. **Profile Management**: Active profile dropdown + add/remove buttons
2. **Configuration**: Config filename + save/load buttons + action buttons
3. **Core Settings**: Cluster type + resources (CPU, RAM, Time)
4. **Remote Connection**: Host + port + username (remote only)
5. **SSH Authentication**: SSH key + refresh + password (remote only)
6. **Auth Options**: Env var + 1Password + home dir (remote only)
7. **Action Buttons**: Advanced settings + SSH setup (SSH setup remote only)
8. **Advanced Section**: Package manager + Python + environment (expandable)

### Visual Themes:
- **Main Action Buttons**: Dark purple/navy (#3e4a61) with white text
- **Icon Buttons**: Gray (save) and orange (load) with dark icons
- **Input Fields**: White background with black borders
- **Add/Remove Buttons**: Small dark purple squares with white +/− symbols
- **Labels**: Black text, standard font, left-aligned
- **Dropdowns**: White with black border and dropdown arrow

### Responsive Behavior:
- Remote-specific rows (4-6) hide when cluster type is "local"
- Advanced settings section toggles visibility
- SSH setup button only appears for remote cluster types
- Component widths adjust to content while maintaining alignment

## Implementation Notes

### Priority Order:
1. Core layout structure (rows 1-3)
2. Remote cluster components (rows 4-7)
3. Advanced settings section
4. Event handlers and state management
5. Visual styling and exact color matching

### State Dependencies:
- Cluster type controls visibility of remote sections
- Advanced settings button controls advanced section visibility
- Profile selection updates all field values
- Component interactions trigger appropriate updates

This specification provides the exact blueprint for implementing each component to match the mockups precisely.