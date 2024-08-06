import React, { Dispatch, forwardRef, SetStateAction } from "react";
import { Formik, Form, FormikProps } from "formik";
import * as Yup from "yup";
import { EditingValue } from "@/components/credentials/EditingValue";

interface AdvancedFormPageProps {
  setRefreshFreq: Dispatch<SetStateAction<number>>;
  setPruneFreq: Dispatch<SetStateAction<number>>;
  currentPruneFreq: number;
  currentRefreshFreq: number;
  indexingStart: Date | null;
  setIndexingStart: Dispatch<SetStateAction<Date | null>>;
}

const AdvancedFormPage = forwardRef<FormikProps<any>, AdvancedFormPageProps>(
  (
    {
      setRefreshFreq,
      setPruneFreq,
      currentPruneFreq,
      currentRefreshFreq,
      indexingStart,
      setIndexingStart,
    },
    ref
  ) => {
    return (
      <div className="py-4 rounded-lg max-w-2xl mx-auto">
        <h2 className="text-2xl font-bold mb-4 text-text-800">
          Advanced Configuration
        </h2>
        <Formik
          innerRef={ref}
          initialValues={{
            pruneFreq: currentPruneFreq,
            refreshFreq: currentRefreshFreq,
            indexingStart: indexingStart,
          }}
          validationSchema={Yup.object().shape({
            pruneFreq: Yup.number().min(0, "Must be a positive number"),
            refreshFreq: Yup.number().min(0, "Must be a positive number"),
            indexingStart: Yup.date().nullable(),
          })}
          onSubmit={async (_, { setSubmitting }) => {
            setSubmitting(false);
          }}
          enableReinitialize={true}
        >
          {({ values, setFieldValue }) => (
            <Form className="space-y-6">
              <div key="prune_freq">
                <EditingValue
                  showNever
                  description="Checking all documents against the source to see if any no longer exist. Documents are deleted based on this. Note: To do this, we must check every document with the source so careful turning up the frequency of this (in minutes). This defaults to 1 day. If you input 0, we will never prune this connector."
                  optional
                  currentValue={values.pruneFreq}
                  onChangeNumber={(value: number) => {
                    setPruneFreq(value);
                    setFieldValue("pruneFreq", value);
                  }}
                  setFieldValue={setFieldValue}
                  type="number"
                  label="Prune Frequency"
                  name="pruneFreq"
                />
              </div>
              <div key="refresh_freq">
                <EditingValue
                  showNever
                  description="This is how frequently we pull new documents from the source (in minutes). If you input 0, we will never pull new documents for this connector."
                  optional
                  currentValue={values.refreshFreq}
                  onChangeNumber={(value: number) => {
                    setRefreshFreq(value);
                    setFieldValue("refreshFreq", value);
                  }}
                  setFieldValue={setFieldValue}
                  type="number"
                  label="Refresh Frequency"
                  name="refreshFreq"
                />
              </div>
              <div key="indexing_start">
                <EditingValue
                  description="Documents prior to this date will not be pulled in"
                  optional
                  currentValue={
                    values.indexingStart ? values.indexingStart : undefined
                  }
                  onChangeDate={(value: Date | null) => {
                    setIndexingStart(value);
                    setFieldValue("indexingStart", value);
                  }}
                  setFieldValue={setFieldValue}
                  type="date"
                  label="Indexing Start Date"
                  name="indexingStart"
                />
              </div>
            </Form>
          )}
        </Formik>
      </div>
    );
  }
);

AdvancedFormPage.displayName = "AdvancedFormPage";
export default AdvancedFormPage;
