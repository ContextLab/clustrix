Cost Monitoring
===============

.. currentmodule:: clustrix.cost_monitoring

Clustrix provides comprehensive cost monitoring and optimization features for major cloud providers. This module enables automatic cost tracking, resource utilization monitoring, and cost optimization recommendations.

Overview
--------

The cost monitoring system supports:

- **AWS**: EC2 instances, Batch, spot instances
- **Google Cloud**: Compute Engine, preemptible VMs, sustained use discounts  
- **Azure**: Virtual Machines, Batch, spot VMs
- **Lambda Cloud**: GPU instances with utilization tracking

Core Classes
------------

ResourceUsage
~~~~~~~~~~~~~

.. autoclass:: ResourceUsage
   :members:
   :undoc-members:
   :show-inheritance:

   Data class containing resource utilization metrics.

   **Attributes:**

   - ``cpu_percent``: CPU utilization percentage
   - ``memory_used_mb``: Memory usage in MB
   - ``memory_total_mb``: Total memory in MB  
   - ``memory_percent``: Memory utilization percentage
   - ``gpu_stats``: Optional GPU utilization data
   - ``network_io_mb``: Optional network I/O in MB
   - ``disk_io_mb``: Optional disk I/O in MB

CostEstimate
~~~~~~~~~~~~

.. autoclass:: CostEstimate
   :members:
   :undoc-members:
   :show-inheritance:

   Data class containing cost estimation information.

   **Attributes:**

   - ``instance_type``: Cloud instance type
   - ``hourly_rate``: Cost per hour in USD
   - ``hours_used``: Number of hours used
   - ``estimated_cost``: Total estimated cost
   - ``currency``: Currency (default: "USD")
   - ``last_updated``: Last update timestamp

CostReport
~~~~~~~~~~

.. autoclass:: CostReport
   :members:
   :undoc-members:
   :show-inheritance:

   Comprehensive cost and usage report.

   **Attributes:**

   - ``timestamp``: Report generation time
   - ``duration_seconds``: Monitoring duration
   - ``resource_usage``: Resource utilization data
   - ``cost_estimate``: Cost estimation data
   - ``provider``: Cloud provider name
   - ``region``: Optional region information
   - ``recommendations``: Cost optimization suggestions
   - ``metadata``: Additional metadata

Base Monitor Class
------------------

BaseCostMonitor
~~~~~~~~~~~~~~~

.. autoclass:: BaseCostMonitor
   :members:
   :undoc-members:
   :show-inheritance:

   Abstract base class for cloud provider cost monitors.

   **Key Methods:**

   - ``get_resource_usage()``: Get current resource utilization
   - ``estimate_cost()``: Estimate costs for given usage
   - ``get_pricing_info()``: Get current pricing information
   - ``start_monitoring()``: Begin cost monitoring session
   - ``stop_monitoring()``: End monitoring and generate report. It prices the
     elapsed wall-clock time with a hardcoded ``estimate_cost("default", ...)``
     -- it takes no instance type and there is no way to give it one, so the
     cost it reports is always the provider's placeholder "default" rate.

Decorators and Utilities
------------------------

cost_tracking_decorator
~~~~~~~~~~~~~~~~~~~~~~~

.. autofunction:: cost_tracking_decorator

   Decorator for automatic cost tracking of functions.

   **Parameters:**

   - ``provider``: Cloud provider name ('aws', 'gcp', 'azure', 'lambda')
   - ``instance_type``: Recorded, but **not** used to price the run. The
     wrapper calls ``monitor.stop_monitoring()``, which prices the elapsed
     time with a hardcoded ``estimate_cost("default", ...)``, so the cost in
     ``result['cost_report']`` is always the provider's placeholder "default"
     rate regardless of what you pass here. The value you passed is echoed
     back unchanged as ``result['instance_type']``, and it is the only place
     it appears. To price a specific instance type, call
     ``get_cost_monitor(provider).estimate_cost(instance_type, hours)``
     yourself.

   **Example:**

   .. code-block:: python

      from clustrix import cost_tracking_decorator, cluster

      @cost_tracking_decorator('aws', 'p3.2xlarge')
      @cluster(cores=8, memory='60GB')
      def train_model():
          # Your training code here
          pass

      # Automatic cost tracking with detailed report
      result = train_model()
      print(f"Cost: ${result['cost_report']['cost_estimate']['estimated_cost']:.2f}")

Utility Functions
-----------------

get_cost_monitor
~~~~~~~~~~~~~~~~

.. autofunction:: get_cost_monitor

   Get the appropriate cost monitor for a cloud provider.

   **Parameters:**

   - ``provider``: Cloud provider name

   **Returns:**

   - ``BaseCostMonitor``: Provider-specific cost monitor instance

   **Example:**

   .. code-block:: python

      from clustrix import get_cost_monitor

      monitor = get_cost_monitor('gcp')
      cost_estimate = monitor.estimate_cost('n2-standard-4', 2.0)

start_cost_monitoring
~~~~~~~~~~~~~~~~~~~~~

.. autofunction:: start_cost_monitoring

   Start cost monitoring for a specific provider.

   **Parameters:**

   - ``provider``: Cloud provider name

   **Returns:**

   - ``BaseCostMonitor``: Active cost monitor instance

generate_cost_report
~~~~~~~~~~~~~~~~~~~~

.. autofunction:: generate_cost_report

   Build a cost report from the monitor's *current* resource usage.

   The real signature is ``generate_cost_report(provider, instance_type="default")``.
   There is no ``duration_seconds`` parameter and no duration override: the
   function hardcodes ``monitor.estimate_cost(instance_type, 1.0)``, so the
   ``cost_estimate`` it returns is always a **one-hour quote** for
   ``instance_type``, not the cost of however long your session has been
   running. It also does not stop or reset monitoring. For a figure based on
   elapsed time, call ``monitor.stop_monitoring()`` instead -- but see the
   caveat under ``cost_tracking_decorator`` about which instance type that
   prices.

   **Parameters:**

   - ``provider``: Cloud provider name
   - ``instance_type``: Instance type to price for one hour (default:
     ``"default"``, the placeholder rate)

   **Returns:**

   - ``dict``: ``timestamp``, ``provider``, ``resource_usage``,
     ``cost_estimate`` and ``recommendations``; or ``None`` if the provider is
     not supported.

get_pricing_info
~~~~~~~~~~~~~~~~

.. autofunction:: get_pricing_info

   Get pricing information for a cloud provider.

   **Parameters:**

   - ``provider``: Cloud provider name

   **Returns:**

   - ``dict``: Pricing information by instance type

Cloud Provider Monitors
-----------------------

Lambda Cloud Monitor
~~~~~~~~~~~~~~~~~~~~

.. autoclass:: clustrix.cost_providers.lambda_cloud.LambdaCostMonitor
   :members:
   :undoc-members:
   :show-inheritance:

   Cost monitoring for Lambda Cloud GPU instances.

   **Features:**

   - Real-time GPU utilization monitoring
   - Accurate pricing for all Lambda instance types
   - Instance recommendations based on usage patterns
   - Monthly cost estimation tools

AWS Cost Monitor
~~~~~~~~~~~~~~~~

.. autoclass:: clustrix.cost_providers.aws.AWSCostMonitor
   :members:
   :undoc-members:
   :show-inheritance:

   Cost monitoring for AWS EC2 and Batch services.

   **Features:**

   - On-demand and spot instance pricing
   - AWS Batch cost estimation
   - Regional pricing comparisons
   - Reserved instance recommendations

Azure Cost Monitor
~~~~~~~~~~~~~~~~~~

.. autoclass:: clustrix.cost_providers.azure.AzureCostMonitor
   :members:
   :undoc-members:
   :show-inheritance:

   Cost monitoring for Azure Virtual Machines and Batch.

   **Features:**

   - Pay-as-you-go and spot VM pricing
   - Azure Batch cost estimation
   - Regional pricing analysis
   - Cost optimization recommendations

GCP Cost Monitor
~~~~~~~~~~~~~~~~

.. autoclass:: clustrix.cost_providers.gcp.GCPCostMonitor
   :members:
   :undoc-members:
   :show-inheritance:

   Cost monitoring for Google Cloud Compute Engine.

   **Features:**

   - On-demand and preemptible instance pricing
   - Sustained use discount calculations  
   - Regional pricing comparisons
   - Google Cloud Batch cost estimation

Usage Examples
--------------

Basic Cost Monitoring
~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: python

   from clustrix import get_cost_monitor
   
   # Get AWS cost monitor
   monitor = get_cost_monitor('aws')
   
   # Estimate costs
   cost_estimate = monitor.estimate_cost('p3.2xlarge', hours_used=2.0)
   print(f"Cost: ${cost_estimate.estimated_cost:.2f}")
   
   # Get current resource usage
   usage = monitor.get_resource_usage()
   print(f"CPU: {usage.cpu_percent}%, Memory: {usage.memory_percent}%")

Automatic Cost Tracking
~~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: python

   from clustrix import cost_tracking_decorator, cluster
   
   @cost_tracking_decorator('gcp', 'n2-standard-8')
   @cluster(cores=8, memory='32GB')
   def data_processing():
       # Your data processing code
       import pandas as pd
       df = pd.read_csv('large_dataset.csv')
       return df.groupby('category').sum()
   
   # Execute with automatic cost tracking
   result = data_processing()
   if result['success']:
       print(f"Processing completed successfully")
       print(f"Estimated cost: ${result['cost_report']['cost_estimate']['estimated_cost']:.2f}")
       print(f"Duration: {result['cost_report']['duration_seconds']:.1f} seconds")

Manual Session Monitoring
~~~~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: python

   from clustrix import start_cost_monitoring, generate_cost_report
   
   # Start monitoring
   monitor = start_cost_monitoring('azure')
   
   # Run your workload
   # ... your code here ...
   
   # Generate report. Despite the name, this is not the cost of the session
   # so far: generate_cost_report hardcodes a 1.0-hour estimate, so the figure
   # below is a one-hour quote for Standard_NC6s_v3. The resource_usage in the
   # same report *is* current.
   report = generate_cost_report('azure', 'Standard_NC6s_v3')
   print(f"One-hour quote: ${report['cost_estimate']['estimated_cost']:.2f}")

Cost Optimization
~~~~~~~~~~~~~~~~~

.. code-block:: python

   from clustrix import get_cost_monitor
   
   monitor = get_cost_monitor('aws')
   
   # Get pricing information
   pricing = monitor.get_pricing_info()
   
   # Compare spot vs on-demand pricing
   on_demand = monitor.estimate_cost('p3.2xlarge', 1.0, use_spot=False)
   spot = monitor.estimate_cost('p3.2xlarge', 1.0, use_spot=True)
   
   savings = ((on_demand.hourly_rate - spot.hourly_rate) / on_demand.hourly_rate) * 100
   print(f"Spot instance savings: {savings:.1f}%")

Regional Pricing Comparison
~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: python

   monitor = get_cost_monitor('gcp')
   
   # Compare pricing across regions
   regional_pricing = monitor.get_region_pricing_comparison('n2-standard-4')
   
   for region, pricing in regional_pricing.items():
       print(f"{region}: ${pricing['on_demand_hourly']:.3f}/hour")

Error Handling
--------------

The cost monitoring system includes robust error handling:

.. code-block:: python

   from clustrix import get_cost_monitor
   
   # An unsupported provider logs a warning and returns None -- it does not
   # raise, so check the result before using it.
   monitor = get_cost_monitor('unsupported_provider')
   if monitor is None:
       print("Provider not supported")

   # An unrecognised instance type does not raise either. It is priced at a
   # placeholder "default" rate, and says so in pricing_warning.
   monitor = get_cost_monitor('aws')
   cost_estimate = monitor.estimate_cost('invalid_instance', 1.0)
   if cost_estimate.pricing_warning:
       print(f"Estimate is not reliable: {cost_estimate.pricing_warning}")

   # pricing_warning is NOT a complete guard, and pricing_source is not
   # trustworthy either. For a *recognised* instance type, the provider
   # monitor calls the pricing client, and the pricing client falls back to
   # its own hardcoded table internally when the live API is unavailable. The
   # monitor only sees "a number came back", so it labels the record
   # pricing_source="api" and leaves pricing_warning=None -- even though the
   # figure came from the same stale table. Verified on a machine with no AWS
   # credentials: estimate_cost('p3.2xlarge', 1.0) returns hourly_rate 3.06,
   # pricing_source 'api', pricing_warning None, while the logger emits
   # "Using hardcoded pricing for p3.2xlarge (last updated: 2025-01-01)".
   # The only reliable signal that fallback pricing was used is that log
   # record, so enable logging if the distinction matters:
   import logging
   logging.getLogger('clustrix.pricing_clients.base').setLevel(logging.WARNING)

Best Practices
--------------

1. **Use Decorators**: For automatic tracking of cluster functions
2. **Monitor Long Jobs**: Use manual monitoring for jobs over 1 hour
3. **Check Recommendations**: Review cost optimization suggestions regularly
4. **Compare Pricing**: Use regional and instance type comparisons
5. **Track Trends**: Save reports to analyze cost trends over time

Notes
-----

- Cost estimates fall back to a hardcoded price table when a live pricing API
  is unavailable. That table is a snapshot, not live pricing: the AWS, Azure
  and GCP tables in ``clustrix/pricing_clients/*_pricing.py`` are dated
  ``2025-01-01`` and the Lambda Cloud one ``2025-01-08``
  (``_hardcoded_pricing_date``). Treat every figure as an order-of-magnitude
  guide, not a quote.
- Resource utilization requires appropriate permissions on the target system
- GPU monitoring requires ``nvidia-smi`` on the target system
- Some cloud providers may have rate limits on pricing API calls
- Spot/preemptible instance availability and pricing can change frequently