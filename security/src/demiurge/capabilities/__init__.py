"""Security Agent capabilities — operations the broker performs on callers' behalf.

Each capability is a registered function that receives validated parameters
and returns a non-sensitive result. Raw secret material never leaves this
package.
"""
