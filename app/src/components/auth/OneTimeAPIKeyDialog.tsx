import React from "react";
import { css } from "@emotion/react";

import {
  Alert,
  Dialog,
  Flex,
  Heading,
  Text,
  TextField,
  View,
} from "@arizeai/components";

import { CodeWrap, PythonBlockWithCopy } from "@phoenix/components/code";
import { CopyToClipboardButton } from "@phoenix/components/CopyToClipboardButton";

import { ExternalLink } from "../ExternalLink";

/**
 * Displays the key one time for the user to copy.
 */
export function OneTimeAPIKeyDialog(props: { jwt: string }) {
  const { jwt } = props;
  return (
    <Dialog title="New API Key Created" isDismissable size="M">
      <Alert variant="success" banner>
        You have successfully created a new API key. The API key will only be
        displayed once below. Please copy and save it in a secure location.
      </Alert>
      <div
        css={css`
          .ac-field {
            width: 100%;
          }
        `}
      >
        <View padding="size-200">
          <Flex direction="row" gap="size-100" alignItems="end">
            <TextField label="API Key" isReadOnly value={jwt} minWidth="100%" />
            <CopyToClipboardButton text={jwt} size="normal" />
          </Flex>
        </View>
        <View padding="size-200" borderTopColor="light" borderTopWidth="thin">
          <Heading level={2} weight="heavy">
            How to Use the API Key
          </Heading>
          <View paddingBottom="size-100" paddingTop="size-100">
            <Text>
              When interacting with Phoenix clients and OTEL SDKs, set the
              environment variable
            </Text>
          </View>
          <CodeWrap>
            <PythonBlockWithCopy value={`PHOENIX_API_KEY=${jwt}`} />
          </CodeWrap>
          <View paddingBottom="size-100" paddingTop="size-100">
            <Text>
              When using OpenTelemetry SDKs pass the API key in the headers
            </Text>
          </View>
          <CodeWrap>
            <PythonBlockWithCopy
              value={`OTEL_EXPORTER_OTLP_HEADERS='authorization=Bearer%20${jwt}'`}
            />
          </CodeWrap>
          <View paddingBottom="size-100" paddingTop="size-100">
            <Text>
              When using the Phoenix REST and GraphQL APIs, pass the API key as
              a{" "}
              <ExternalLink href="https://swagger.io/docs/specification/authentication/bearer-authentication/">
                bearer token
              </ExternalLink>
              .
            </Text>
          </View>
          <CodeWrap>
            <PythonBlockWithCopy value={`Authorization: Bearer ${jwt}`} />
          </CodeWrap>
        </View>
      </div>
    </Dialog>
  );
}