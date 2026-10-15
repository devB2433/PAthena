# LedgerBridge: asynchronous invoice export design

## Scope and business ownership

This is a synthetic design for evaluating security requirements, not a deployed product.
Each customer owns private invoices containing names, addresses, bank account details and invoice amounts.
Ordinary users may work with their customer's invoices. A delegated support administrator may work across customers only within the customer's explicit delegation.
Customer identity and delegation are established by the existing identity service. This change does not redesign that service.

## Request and processing

An authenticated browser calls POST /exports with a list of invoice identifiers. These identifiers are chosen by the user; knowing an identifier does not grant invoice access.
The API saves a job containing its requester, customer, chosen invoices and requested display title. It sends the job identifier to a private work queue.
A background worker consumes the identifier, loads the job and reads invoices with a service account that can read all customers' invoices.
It writes a CSV result to a private object store. Users cannot directly address the private object store; result bytes are delivered by the application download endpoint.
The title is displayed in the UI. This design does not use it as a path, URL, command, SQL fragment or template program.

## Job access and lifecycle

GET /exports/{job} returns status and GET /exports/{job}/download returns the result. Job identifiers are sequential integers and are visible in support screens.
Ordinary users may inspect and download only their own jobs. Delegated administrators may access jobs within their explicit delegation.
The API also supports cancellation. A job cancelled before completion must not subsequently publish a result, including if the worker is concurrently completing it.
The queue delivers at least once. Retrying the same job must not publish a second final result or create a second chargeable export.
Completed files expire at a configured time. After expiry, the application must no longer return their contents, even if the object remains in private storage pending cleanup.
This proposal does not specify a file deletion deadline or immediate revocation when a support delegation changes.

## Operations and capacity

Generating a large export can consume substantial CPU and object storage. Authenticated customers can submit repeated jobs; the service is shared and one customer must not prevent others from obtaining exports.
The admission-control mechanism and its numerical limits are not yet chosen.
For dispute investigation, the service must make it possible to identify who requested, downloaded or cancelled each job and when, including delegated administrator actions.
Application operational logs are accessible to a broader support group than the group authorized to read invoice bodies. The existing identity service uses bearer session credentials.
The production audit platform must actually retain the export activity records for 90 days. This is an operations responsibility; this repository only provides the log producer and retention configuration interface.

## Optional features, non-goals and engineering notes

Operators may enable a completion-count metric OR a completion audit event for performance monitoring. Both are not required, and this choice does not replace the dispute-investigation records above.
Adding a new MFA implementation, selecting a WAF and changing the corporate employee training programme are outside this change.
The project aims for 80% unit-test coverage. This is an engineering target, not a condition on whether a user is allowed to export.
There is no payment-card number, PAN processing, payment authorization or cardholder-data-environment determination in this document. PCI scope must not be invented.
